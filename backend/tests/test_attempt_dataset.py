import json
from types import SimpleNamespace
import pytest
from pianokt_backend.pipeline.attempts import export_attempts, utc
from pianokt_backend.storage import Objects


def fixture(tmp_path):
    store = Objects(str(tmp_path))
    events = dict(kc_feature_names=['a','b','c','d','e','f'], onset_seconds=[0, .5],
                  pitches=[[[48],[60]], [[],[62]]], intervals_seconds=[[None,None,None],[None,.5,.5]],
                  kc_vectors=[[False,2,True,None,2,None],[False,1,False,50,1,1]],
                  reject_reason={'event':[0,1], 'note':[[[0],[0]],[[],[1]]]},
                  timing_offset_ms=[[[0],[0]],[[],[None]]], pitch_offset_semitones=[[[0],[0]],[[],[None]]], extra_notes=[[],[]])
    def attempt(aid, vid, start, seq):
        return dict(source_environment='production',attempt_id=aid,visit_id=vid,visit_attempt_sequence=seq,user_id='private-auth-id',learner_key='learner',
                    song_id='song',song_source='builtin', started_at=start,ended_at='2026-01-02T01:01:00Z',
                    registered_at='2025-01-01T00:00:00Z',time_playing=1,is_played_in_full=False,exit_status='abandoned')
    context = dict(visits=[dict(source_environment='production',id='v2',user_id='private-auth-id',learner_key='learner',started_at='2026-01-02T01:00:00Z'),
                           dict(source_environment='production',id='v1',user_id='private-auth-id',learner_key='learner',started_at='2026-01-02T08:00:00+08:00')],
                   attempts=[attempt('a3','v2','2026-01-02T01:00:00Z',1),attempt('a2','v1','2026-01-02T08:02:00+08:00',2),attempt('a1','v1','2026-01-02T08:01:00+08:00',1)], events=[])
    for aid in ('a1','a2','a3'):
        store.json(f'alignment/{aid}/result.json', dict(source_environment='production',attempt_id=aid,alignment_run_id=aid,learner_key='learner',score_version='score',
            alignment_completed_at='2026-01-02T02:00:00Z', alignment_quality_valid=aid!='a2',events_data=events))
    tables = SimpleNamespace(rows=lambda _: [{'event_id':a} for a in ('a1','a2','a3','duplicate')])
    return store, tables, context


def test_ordering_is_utc_and_indices_precede_quality_filtering(tmp_path):
    store,tables,context = fixture(tmp_path)
    manifest=export_attempts(store,tables,'2026-02-01T00:00:00Z','datasets/test',context)
    rows=[json.loads(line) for line in store.read('datasets/test/attempts.jsonl').splitlines()]
    assert [(r['visit_index'],r['attempt_index']) for r in rows] == [(1,1),(2,3)]
    assert rows[0]['notes_evaluated']==3 and rows[0]['notes_successful']==2
    assert rows[0]['chords_evaluated']==1 and rows[0]['chords_successful']==1
    assert rows[0]['exit_status']=='quit'
    assert not {'attempt_id','visit_id','started_at','ended_at','attempt_created_at','session_index'} & rows[0].keys()
    assert b'private-auth-id' not in b''.join(store.read(k) for k in store.keys('datasets'))
    assert manifest['skipped']=={'quality_rejected':1}
    assert export_attempts(store,tables,'2026-02-01T00:00:00Z','datasets/test',context)==manifest
    mapping=json.loads(store.read('datasets/test/index_map.json'))
    assert mapping[1]['attempt_id']=='a3'


def test_export_refuses_multiple_alignment_versions(tmp_path):
    store,tables,context=fixture(tmp_path)
    r=json.loads(store.read('alignment/a1/result.json')); r['alignment_run_id']='duplicate'
    store.json('alignment/duplicate/result.json',r)
    with pytest.raises(ValueError,match='one alignment version'):
        export_attempts(store,tables,'2026-02-01T00:00:00Z','datasets/test',context)


def test_missing_visit_never_fabricates_history(tmp_path):
    store,tables,context=fixture(tmp_path); context['visits']=[]
    manifest=export_attempts(store,tables,'2026-02-01T00:00:00Z','datasets/test',context)
    assert manifest['count']==0 and manifest['skipped']['missing_context']==3


def test_late_received_history_not_visible_before_cutoff(tmp_path):
    store,tables,context=fixture(tmp_path)
    context['attempts'][-1]['received_at']='2026-03-01T00:00:00Z'
    export_attempts(store,tables,'2026-02-01T00:00:00Z','datasets/test',context)
    rows=[json.loads(line) for line in store.read('datasets/test/attempts.jsonl').splitlines()]
    assert [r['attempt_index'] for r in rows]==[2]


def test_bad_event_shape_fails_before_any_files_written(tmp_path):
    store,tables,context=fixture(tmp_path)
    r=json.loads(store.read('alignment/a1/result.json')); r['events_data']['pitches'][0]=[[48]]
    # A separate in-memory object source permits testing an invalid input without overwriting immutable results.
    original=store.read
    store.read=lambda key: json.dumps(r).encode() if key=='alignment/a1/result.json' else original(key)
    with pytest.raises(ValueError,match='hand shape'):
        export_attempts(store,tables,'2026-02-01T00:00:00Z','datasets/test',context)
    assert store.keys('datasets')==[]


def test_timezone_required():
    with pytest.raises(ValueError,match='timezone'): utc('2026-01-01T00:00:00')


def test_exercise_identity_does_not_depend_on_early_exit_crop(tmp_path):
    from pianokt_backend.pipeline.attempts import build_record
    store,_,context=fixture(tmp_path)
    r=json.loads(store.read('alignment/a1/result.json'))
    first=build_record(r,context['attempts'][0],1,1)
    r['score_version']='shorter-observed-crop'
    second=build_record(r,context['attempts'][0],1,2)
    assert first['exercise_id']==second['exercise_id']


def test_explicit_run_selection_and_manifest_validation(tmp_path):
    from pianokt_backend.publication import read_bundle
    store,tables,context=fixture(tmp_path)
    r=json.loads(store.read('alignment/a1/result.json')); r['alignment_run_id']='duplicate'
    store.json('alignment/duplicate/result.json',r)
    export_attempts(store,tables,'2026-02-01T00:00:00Z','datasets/selected',context,{'a1':'duplicate'})
    manifest,files=read_bundle(store,'datasets/selected')
    assert manifest['count']==1
    assert json.loads(files['index_map.json'])[0]['alignment_run_id']=='duplicate'


def test_environment_filtered_before_indices_and_unknown_never_promoted(tmp_path):
    store,tables,context=fixture(tmp_path)
    context['visits'].append(dict(id='dev-visit',learner_key='learner',source_environment='development',started_at='2025-01-01T00:00:00Z'))
    dev=dict(context['attempts'][0],attempt_id='dev',visit_id='dev-visit',source_environment='development',started_at='2025-01-01T00:00:01Z')
    unknown=dict(context['attempts'][0],attempt_id='unknown',source_environment='unknown',started_at='2025-01-01T00:00:02Z')
    context['attempts'].extend([dev,unknown])
    export_attempts(store,tables,'2026-02-01T00:00:00Z','datasets/filtered',context)
    rows=[json.loads(line) for line in store.read('datasets/filtered/attempts.jsonl').splitlines()]
    assert [(r['visit_index'],r['attempt_index']) for r in rows]==[(1,1),(2,3)]
    raw=store.read('datasets/filtered/attempt_metadata.json')
    assert b'"attempt_id":"dev"' not in raw and b'"attempt_id":"unknown"' not in raw


@pytest.mark.parametrize('mixed', [False, True])
def test_actual_kc_version_propagates_or_mixed_export_fails(tmp_path, mixed):
    from pianokt_backend.pipeline.training import export_dataset
    store, tables, context = fixture(tmp_path)
    original = store.read
    def read(key):
        raw = original(key)
        if key.startswith('alignment/'):
            result = json.loads(raw)
            if not mixed or result['attempt_id'] == 'a1':
                result['kc_labeler_version'] = '1.2.0'
            return json.dumps(result).encode()
        return raw
    store.read = read
    if mixed:
        with pytest.raises(ValueError, match='Mixed KC'):
            export_attempts(store, tables, '2026-02-01T00:00:00Z', 'datasets/new', context)
        with pytest.raises(ValueError, match='Mixed KC'):
            export_dataset(store, tables, '2026-02-01T00:00:00Z', 'datasets/train.json', context)
        assert store.keys('datasets') == []
    else:
        manifest = export_attempts(store, tables, '2026-02-01T00:00:00Z', 'datasets/new', context)
        assert manifest['kc_labeler_version'] == '1.2.0'
        for record in map(json.loads, store.read('datasets/new/attempts.jsonl').splitlines()):
            assert record['kc_labeler_version'] == '1.2.0'
            assert record['events_data']['reject_reason']['note'] == [[[0], [0]], [[], [1]]]
        assert all(r['kc_labeler_version'] == '1.2.0' for r in json.loads(store.read('datasets/new/index_map.json')))
        export_dataset(store, tables, '2026-02-01T00:00:00Z', 'datasets/train.json', context)
        training = json.loads(store.read('datasets/train.json'))
        assert training['kc_labeler_version'] == '1.2.0'
        assert 'feature_version' not in training and 'feature_version' not in manifest
