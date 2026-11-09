import copy
import json
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from pianokt_backend.pykt_acceptance import DiagnosticModel, diagnostic_catalog, diagnostic_report
from pianokt_backend.pykt_catalog import CatalogCandidateProvider
from pianokt_backend.pykt_history import (build_snapshot, sync_history, verify_binding, binding_path,
                                         student_identity, read_snapshot)
from pianokt_backend.pykt_integration import Compatibility, CompatibilityError, PyKTClient, KC_FEATURE_NAMES, canonical
from pianokt_backend.pykt_recommendation import RecommendationConfig, RecommendationService, fixed_targets, fingerprint

LEARNER = str(UUID(int=123))


def client(model=None):
    model = model or DiagnosticModel()
    http = httpx.Client(base_url='http://mock', transport=httpx.MockTransport(model.handle))
    return PyKTClient(http, Compatibility(model.model, model.feature, '1.2.0', 'a'*64))


def check(state):
    return dict(event_count=0, status='diagnostic', snapshot_digest='empty')


def test_golden_onset_weighting_gain_and_determinism():
    model = DiagnosticModel(); c = client(model)
    catalog = diagnostic_catalog()
    # Exact duplicate vectors with 2:1 frequency expose accidental unique-KC averaging.
    a = copy.deepcopy(catalog[0]['rows'][0]); b = copy.deepcopy(catalog[0]['rows'][-1])
    catalog[0]['rows'] = [a, a, b]
    report = RecommendationService(c).recommend('s', catalog, check_history=check)
    first = next(d for d in report['candidates'] if d['segment_id'] == catalog[0]['segment_id'])
    assert first['predicted_accuracy'] == pytest.approx((.6+.6+.8)/3)
    assert first['predicted_accuracy'] != pytest.approx(.7)
    assert report['items'][0]['predicted_learning_gain'] == pytest.approx(.04)
    assert report['items'][-1]['predicted_learning_gain'] == pytest.approx(-.02)
    for item in report['items']:
        expected = sum(t['weight'] * (item['rollout_statistics']['forecast_statistics'][t['candidate_id']]['mean']
                       - report['p_before'][t['candidate_id']]) for t in report['targets'])
        assert item['score'] == pytest.approx(expected)
    posts = [b for _, p, b in model.requests if p.endswith('/evaluate')]
    assert len(posts) == 3
    assert all(p['forecast_candidates'] == posts[0]['forecast_candidates'] for p in posts)
    assert all(p['include_steps'] is False and p['retain_branch'] is False for p in posts)
    assert all(p['num_trajectories'] == 8 for p in posts)
    again = RecommendationService(c).recommend('s', list(reversed(catalog)), check_history=check)
    assert [i['segment_id'] for i in report['items']] == [i['segment_id'] for i in again['items']]
    assert report['target_set_digest'] == again['target_set_digest']
    assert model.revision == 0
    predictions = [b for _, p, b in model.requests if p.endswith('/predict')]
    assert sum(len(b['candidates']) for b in predictions) == report['unique_kc_count']


@pytest.mark.parametrize('probability,expected', [(.6,3),(.8,3),(.599,0),(.801,0)])
def test_sweet_spot_boundaries(probability, expected):
    model = DiagnosticModel(); model.probability = lambda _: probability
    report = diagnostic_report(client(model), RecommendationConfig(), 'offline_mock')
    assert report['sweet_spot_count'] == expected


@pytest.mark.parametrize('limit,value', [('max_rollout_steps',2),('max_trajectories',7),
    ('max_rollout_work',23),('max_output_steps',1),('max_candidates',1)])
def test_complete_song_limits_never_truncate(limit, value):
    model = DiagnosticModel(); model.limits[limit] = value
    report = diagnostic_report(client(model), RecommendationConfig(), 'offline_mock')
    assert report['rollout_count'] == 0
    assert all(d['reason'] == 'full_song_rollout_limit' for d in report['skipped'])
    assert not any(p.endswith('/evaluate') for _,p,_ in model.requests)


def test_unknown_ratios_reject_candidate(monkeypatch):
    c = client(); original = c.predict
    def unknown(*args):
        result = original(*args)
        result['onsets'][0].update(unknown_question=True, unknown_fields=['kc_0'])
        return result
    monkeypatch.setattr(c, 'predict', unknown)
    report = RecommendationService(c).recommend('s', diagnostic_catalog(), check_history=check)
    skipped = next(d for d in report['skipped'] if d['reason'] == 'unknown_ratio_exceeded')
    assert skipped['unknown_question_ratio'] == pytest.approx(1/3)
    assert skipped['unknown_field_ratio'] == pytest.approx(1/18)


@pytest.mark.parametrize('change', ['revision','model','history'])
def test_entire_round_rejected_on_change(monkeypatch, change):
    model = DiagnosticModel(); c = client(model); original = c.rollout
    def rollout(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'revision': model.revision += 1
        elif change == 'model': model.model = 'changed'
        return result
    monkeypatch.setattr(c, 'rollout', rollout)
    calls = 0
    def history(state):
        nonlocal calls
        calls += 1
        return dict(event_count=0, snapshot_digest=str(calls) if change == 'history' else 'stable')
    with pytest.raises(CompatibilityError):
        RecommendationService(c).recommend('s', diagnostic_catalog(), check_history=history)


def test_fixed_targets_deterministic_frequency_and_bound():
    rows = [{'kc_vector':[False, i, False, None, i, None]} for i in range(1, 100)]
    targets = fixed_targets(rows * 3, 64, 7)
    assert len(targets) <= 64
    assert sum(t['weight'] for t in targets) == pytest.approx(1)
    assert [{k:v for k,v in t.items() if k != 'row'} for t in targets] == [
        {k:v for k,v in t.items() if k != 'row'} for t in fixed_targets(list(reversed(rows*3)),64,7)]


def storage_fixture():
    raw = (Path(__file__).parent/'fixtures/pykt/moonlight-sonata.mid').read_bytes()
    h = sha256(raw).hexdigest()
    row = dict(song_id=str(UUID(int=1)),segment_id=str(UUID(int=2)),segment_index=1,title='Moonlight',
               midi_sha256=h,storage_path=f'midi/{h}.mid',midi_metadata={})
    proof = {row['segment_id']:dict(midi_sha256=h,context='standalone_reference',kc_labeler_version='1.2.0',evidence='test')}
    return raw,row,proof


@pytest.mark.parametrize('failure', ['hash','path','size','timeout','context','needs_context'])
def test_catalog_download_failures_are_individual(monkeypatch, failure):
    raw,row,proof = storage_fixture()
    if failure == 'hash': raw += b'x'
    if failure == 'path': row['storage_path'] = '../private.mid'
    if failure == 'size': monkeypatch.setattr('pianokt_backend.pykt_catalog.MAX_BYTES', 1)
    if failure == 'context': proof = {}
    if failure == 'needs_context': row['midi_metadata']['requires_full_context'] = True
    def fetch(req):
        assert req.url.path.startswith('/storage/v1/object/public/piano-midi/midi/')
        assert 'authorization' not in req.headers
        if failure == 'timeout': raise httpx.ReadTimeout('no response')
        return httpx.Response(200, content=raw)
    with httpx.Client(transport=httpx.MockTransport(fetch)) as http:
        result = CatalogCandidateProvider(http,'https://storage.test',context_provenance=proof).load([row])
    assert result[0]['status'] == 'skipped' and result[0]['reason']


def test_catalog_full_loading_and_hash_cache():
    raw,row,proof = storage_fixture(); calls = []
    def fetch(req): calls.append(req); return httpx.Response(200,content=raw)
    second = dict(row, segment_id=str(UUID(int=3)),segment_index=2)
    proof[second['segment_id']] = proof[row['segment_id']]
    with httpx.Client(transport=httpx.MockTransport(fetch)) as http:
        provider = CatalogCandidateProvider(http,'https://storage.test',context_provenance=proof)
        result = provider.load([row, second])
        assert len(result) == 2 and all(x['status']=='eligible' for x in result)
        assert len(calls) == 1 and len(provider.extractor._cache) == 1
        assert result[0]['rows'][0]['segment_id'] != result[1]['rows'][0]['segment_id']


def history_fixture(tmp_path, mutate=None):
    from test_direct_export import setup
    from pianokt_backend.storage import Objects
    store,_,context,rows = setup(tmp_path/'old')
    new = Objects(str(tmp_path/'new'))
    for a in context['attempts']:
        a.update(source_environment='test',learner_key=LEARNER)
    for row in rows:
        r = json.loads(store.read(row['result_key']))
        r.update(source_environment='test',learner_key=LEARNER,kc_labeler_version='1.2.0')
        r['events_data']['kc_feature_names'] = KC_FEATURE_NAMES
        if mutate: mutate(r)
        row.update(source_environment='test',learner_key=LEARNER,summary=r['summary'])
        new.json(row['result_key'],r)
        row['result_digest'] = sha256(new.read(row['result_key'])).hexdigest()
    return rows,context['attempts'],new


def test_history_committed_validation_order_response_and_skip(tmp_path):
    rows,ctx,store = history_fixture(tmp_path)
    snap = build_snapshot(rows,ctx,store,LEARNER,'test')
    assert [a['attempt_id'] for a in snap['attempts']] == ['a1','a3']
    assert snap['event_count'] == 4
    assert [e['response'] for a in snap['attempts'] for e in a['events']] == [1,0,1,0]
    assert len(snap['skipped']) == 1
    assert snap['student_id'] != student_identity('development',LEARNER)


@pytest.mark.parametrize('failure', ['digest','key','learner','context','version','boolean_label','names','nan'])
def test_history_integrity_rejects(tmp_path, failure):
    def mutate(r):
        if failure=='version': r['kc_labeler_version']='old'
        elif failure=='boolean_label': r['events_data']['reject_reason']['event'][0] = True
        elif failure=='names': r['events_data']['kc_feature_names'] = list(reversed(KC_FEATURE_NAMES))
        elif failure=='nan': r['events_data']['onset_seconds'][0] = -1
    rows,ctx,store = history_fixture(tmp_path,mutate)
    if failure=='digest': rows[0]['result_digest']='bad'
    elif failure=='key': rows[0]['result_key']='bad'
    elif failure=='learner': rows[0]['learner_key']=str(UUID(int=999))
    elif failure=='context': ctx=[]
    with pytest.raises(ValueError): build_snapshot(rows,ctx,store,LEARNER,'test')


class SyncModel(DiagnosticModel):
    def __init__(self):
        super().__init__(); self.limits['max_batch_events']=2
        self.events=[]; self.batch_requests=[]; self.lose_ack=False

    def handle(self,req):
        if not req.url.path.endswith('/observe-batch'): return super().handle(req)
        body=json.loads(req.content); self.batch_requests.append(body)
        student=req.url.path.split('/')[-2]
        ids=[e['event_id'] for e in self.events]
        duplicate=all(e['event_id'] in ids for e in body['events'])
        if not duplicate:
            assert body['expected_revision']==self.revision
            self.events.extend(body['events']); self.revision+=len(body['events'])
        if self.lose_ack:
            self.lose_ack=False
            raise httpx.ReadTimeout('lost after commit')
        return httpx.Response(200,json=dict(state=self.state(student),atomic=True,
            observations=[dict(event_id=e['event_id'],duplicate=duplicate,probability=.7) for e in body['events']]))


def test_sync_dry_run_resume_idempotence_binding_and_conflicts(tmp_path):
    rows,ctx,store=history_fixture(tmp_path)
    snap=build_snapshot(rows,ctx,store,LEARNER,'test')
    model=SyncModel(); c=client(model); root=tmp_path/'journals'
    assert sync_history(c,snap,root)['dry_run']
    assert not root.exists() and not model.batch_requests
    model.lose_ack=True
    with pytest.raises(httpx.ReadTimeout): sync_history(c,snap,root,apply=True)
    assert model.revision==2
    proof=sync_history(c,snap,root,apply=True)
    assert proof['event_count']==4 and model.revision==4
    assert model.batch_requests[0]==model.batch_requests[1]
    count=len(model.batch_requests)
    assert sync_history(c,snap,root,apply=True)==proof
    assert len(model.batch_requests)==count
    binding=json.loads(binding_path(root,snap['student_id']).read_text())
    verify_binding(binding,snap,c._state(snap['student_id']),c)
    altered=copy.deepcopy(snap); altered['snapshot_digest']='changed'
    with pytest.raises(CompatibilityError): verify_binding(binding,altered,c._state(snap['student_id']),c)
    altered=copy.deepcopy(snap); altered['attempts'].reverse()
    with pytest.raises(CompatibilityError): sync_history(c,altered,root,apply=True)
    model.revision+=1
    with pytest.raises(CompatibilityError): sync_history(c,snap,root,apply=True)


def test_nonempty_history_without_journal_refused(tmp_path):
    rows,ctx,store=history_fixture(tmp_path); snap=build_snapshot(rows,ctx,store,LEARNER,'test')
    model=SyncModel();model.revision=1
    with pytest.raises(CompatibilityError,match='Nonempty'): sync_history(client(model),snap,tmp_path/'journal',apply=True)


def test_database_scope_uses_authenticated_user_and_environment():
    calls=[]
    class Connection:
        def execute(self,sql,args):
            calls.append((sql,args))
            if 'select learner_key' in sql: return SimpleNamespace(fetchone=lambda:dict(learner_key=LEARNER))
            return SimpleNamespace(fetchall=lambda:[])
    result=read_snapshot(Connection(),None,'authenticated-user','test')
    assert result['event_count']==0
    assert all(args[0]=='authenticated-user' for _,args in calls)
    assert all('user_id=%s' in sql for sql,_ in calls)
    assert all('test' in args for _,args in calls[1:])


def test_production_gate_and_api_no_silent_fallback(monkeypatch):
    from pianokt_backend import api
    from fastapi.testclient import TestClient
    monkeypatch.setenv('PIANOKT_RECOMMENDER','pykt');monkeypatch.setenv('PIANOKT_ENV','production')
    monkeypatch.setattr(api,'objects',lambda:None)
    monkeypatch.setattr(api,'connect',lambda:pytest.fail('Must not connect to production DB'))
    api.app.dependency_overrides[api.user]=lambda:'authenticated-user'
    try:
        response=TestClient(api.app).post('/recommendations')
        assert response.status_code==409 and 'development or test' in response.json()['detail']
    finally: api.app.dependency_overrides.clear()


def test_sync_append_only_and_cold_start(tmp_path):
    rows,ctx,store=history_fixture(tmp_path)
    full=build_snapshot(rows,ctx,store,LEARNER,'test')
    first=build_snapshot([r for r in rows if r['attempt_id']=='a1'],ctx,store,LEARNER,'test')
    model=SyncModel(); c=client(model)
    root=tmp_path/'sync'
    sync_history(c,first,root,apply=True)
    assert model.revision==2
    result=sync_history(c,full,root,apply=True)
    assert result['event_count']==4 and model.revision==4
    assert [e['event_id'] for e in model.events] == ['a1:onset:0','a1:onset:1','a3:onset:0','a3:onset:1']
    empty=build_snapshot([],[],None,str(UUID(int=999)),'test')
    other=SyncModel(); cold=sync_history(client(other),empty,tmp_path/'cold',apply=True)
    assert cold['event_count']==0 and not other.batch_requests


def test_waiting_and_quality_are_not_observed(tmp_path):
    def mutate(r):
        if r['attempt_id']=='a1': r['practice_metadata']={'settings':{'waiting':True}}
    rows,ctx,store=history_fixture(tmp_path,mutate)
    snap=build_snapshot(rows,ctx,store,LEARNER,'test')
    assert [a['attempt_id'] for a in snap['attempts']]==['a3']
    assert {s['skip_reason'] for s in snap['skipped']}=={'waiting','quality_rejected'}


def test_duplicate_catalog_identity_never_enters_prediction():
    raw,row,proof=storage_fixture()
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('No downloads for duplicate identity'))) as http:
        result=CatalogCandidateProvider(http,'https://storage',context_provenance=proof).load([row,row])
    assert all(r['reason']=='duplicate_catalog_identity' for r in result)


def test_api_pykt_persistence_feedback_shape_and_authenticated_scope(monkeypatch):
    from pianokt_backend import api, pykt_runtime
    from fastapi.testclient import TestClient
    uid=str(UUID(int=501)); rid=str(UUID(int=601)); calls=[]
    report=diagnostic_report(client(),RecommendationConfig(),'offline_mock')
    report['is_demo']=False
    monkeypatch.setenv('PIANOKT_RECOMMENDER','pykt')
    monkeypatch.setattr(pykt_runtime,'database_guard',lambda:None)
    def recommend(user,connect,objects):
        assert user==uid
        return report,None
    monkeypatch.setattr(pykt_runtime,'recommend_for_user',recommend)
    monkeypatch.setattr(api,'objects',lambda:None)
    @contextmanager
    def connection():
        class DB:
            def execute(self,sql,args):
                calls.append((sql,args))
                assert args[0]==uid
                return SimpleNamespace(fetchone=lambda:{'id':rid})
        yield DB()
    monkeypatch.setattr(api,'connect',connection)
    api.app.dependency_overrides[api.user]=lambda:uid
    try:
        response=TestClient(api.app).post('/recommendations')
        assert response.status_code==200
        body=response.json()
        assert body['recommendation_id']==rid and body['is_demo'] is False
        assert all({'song_id','segment_id','rank','score','reason'} <= set(item) for item in body['items'])
        assert len(calls)==1 and 'insert into public.piano_recommendations' in calls[0][0]
    finally: api.app.dependency_overrides.clear()


def test_model_unavailable_never_returns_demo(monkeypatch):
    from pianokt_backend import api, pykt_runtime
    from fastapi.testclient import TestClient
    monkeypatch.setenv('PIANOKT_RECOMMENDER','pykt')
    monkeypatch.setattr(pykt_runtime,'database_guard',lambda:None)
    monkeypatch.setattr(api,'objects',lambda:None)
    def fail(*args): raise httpx.ConnectError('secret URL must not appear')
    monkeypatch.setattr(pykt_runtime,'recommend_for_user',fail)
    monkeypatch.setattr(api,'connect',lambda:pytest.fail('No recommendation write on model failure'))
    api.app.dependency_overrides[api.user]=lambda:'user'
    try:
        response=TestClient(api.app).post('/recommendations')
        assert response.status_code==503 and 'secret' not in response.text
    finally: api.app.dependency_overrides.clear()


def test_forecast_missing_targets_refuses_gain(monkeypatch):
    c=client();original=c.rollout
    def bad(*a,**kw):
        result=original(*a,**kw);result['forecast_statistics']={};return result
    monkeypatch.setattr(c,'rollout',bad)
    with pytest.raises(CompatibilityError,match='Forecast target'):
        RecommendationService(c).recommend('s',diagnostic_catalog(),check_history=check)


def test_revision_change_during_final_history_io_rejected():
    model=DiagnosticModel();c=client(model);count=0
    def recheck(state):
        nonlocal count
        count+=1
        if count==2: model.revision+=1
        return check(state)
    with pytest.raises(CompatibilityError,match='state changed'):
        RecommendationService(c).recommend('s',diagnostic_catalog(),check_history=recheck)
