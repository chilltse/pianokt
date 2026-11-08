"""Integration tests execute the real Python SQL against isolated PGlite PostgreSQL."""
import json
import os
import subprocess
from datetime import datetime
from uuid import UUID
import pytest
from fastapi import HTTPException
from pianokt_backend.activity import Activity, save_activity

U='00000000-0000-0000-0000-000000000001'
OTHER='00000000-0000-0000-0000-000000000002'
V='10000000-0000-0000-0000-000000000001'
A='20000000-0000-0000-0000-000000000001'

class Connection:
    def __init__(self,process): self.process=process
    def execute(self,sql,params=()):
        params = () if params is None else params
        def encode(v):
            if hasattr(v,'obj'): return v.obj
            if isinstance(v,datetime): return v.isoformat()
            return str(v)
        i=0
        while '%s' in sql:
            i+=1
            # psycopg sends Python floats as float8. PGlite otherwise infers
            # numeric from the INSERT target and hides float8->numeric loss.
            cast='::double precision' if isinstance(params[i-1],float) else ''
            sql=sql.replace('%s',f'${i}{cast}',1)
        self.process.stdin.write(json.dumps(dict(sql=sql,params=params),default=encode)+'\n'); self.process.stdin.flush()
        response=json.loads(self.process.stdout.readline())
        if 'error' in response: raise RuntimeError(response['error'])
        self.rows=response['rows']
        for row in self.rows:
            for k,v in row.items():
                if (k.endswith('_at') or k=='client_ts') and isinstance(v,str): row[k]=datetime.fromisoformat(v.replace('Z','+00:00'))
        return self
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows

@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("PIANOKT_ENV", "production")
    if not os.getenv('PGLITE_MODULE'): pytest.skip('Set PGLITE_MODULE for isolated PostgreSQL integration tests')
    process=subprocess.Popen(['node','backend/tests/activity_db.mjs'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert json.loads(process.stdout.readline())['ready']
        c=Connection(process)
        c.execute("insert into auth.users(id,created_at) values(%s,'2025-01-01'),(%s,'2025-01-01')",(U,OTHER))
        yield c
    finally:
        process.stdin.close(); process.wait(timeout=10)


def packet(sequence=1,state='active',event_sequence=0,event_type='play_started',attempt=A):
    return Activity.model_validate(dict(owner_id=U,
        visit=dict(id=V,started_at='2026-01-01T00:00:00Z',occurred_at='2026-01-01T00:01:00Z',sequence=sequence,state=state),
        attempt=dict(attempt_id=attempt,visit_attempt_sequence=1 if attempt==A else 2,song_id='song',song_source='local',started_at='2026-01-01T00:00:10Z'),
        events=[dict(event_id=f'30000000-0000-0000-0000-{event_sequence+1:012}',event_sequence=event_sequence,event_type=event_type,occurred_at=f'2026-01-01T00:00:{10+event_sequence:02}Z',song_time_sec=event_sequence,elapsed_playing_sec=event_sequence,metadata={})]))


def test_duplicate_delayed_and_cross_user_events(db):
    start=packet(); save_activity(db,U,start); save_activity(db,U,start)
    finish=packet(3,event_sequence=2,event_type='finished'); save_activity(db,U,finish)
    pause=packet(2,event_sequence=1,event_type='paused'); save_activity(db,U,pause)
    assert db.execute('select count(*)::int n from student_attempt_events').fetchone()['n']==3
    assert db.execute('select end_reason from student_attempts').fetchone()['end_reason']=='finished'
    assert db.execute('select last_sequence from student_visits').fetchone()['last_sequence']==3
    with pytest.raises(HTTPException,match='another signed-in user'): save_activity(db,OTHER,start)
    stolen=start.model_copy(update={'owner_id':UUID(OTHER)})
    with pytest.raises(HTTPException,match='Visit identity conflict'): save_activity(db,OTHER,stolen)
    mutated=start.model_copy(deep=True); mutated.events[0].song_time_sec=99
    with pytest.raises(HTTPException,match='event identity conflict'): save_activity(db,U,mutated)


def test_two_attempts_share_visit_and_pagehide_can_resume(db):
    body=packet(2,state='pagehide'); save_activity(db,U,body)
    assert db.execute('select end_is_inferred from student_visits').fetchone()['end_is_inferred']
    second=packet(3,attempt='20000000-0000-0000-0000-000000000002')
    second.events[0].event_id=UUID('40000000-0000-0000-0000-000000000001')
    save_activity(db,U,second)
    assert db.execute('select ended_at from student_visits').fetchone()['ended_at'] is None
    assert db.execute('select count(*)::int n from student_attempts').fetchone()['n']==2
    save_activity(db,U,body) # delayed close cannot end resumed visit
    assert db.execute('select ended_at from student_visits').fetchone()['ended_at'] is None


def test_rls_isolation_and_read_only_student_access(db):
    save_activity(db,U,packet())
    db.execute('set role authenticated')
    db.execute("select set_config('request.jwt.claim.sub',%s,false)",(OTHER,))
    assert db.execute('select * from student_visits').fetchall()==[]
    assert db.execute('select * from student_attempt_events').fetchall()==[]
    with pytest.raises(RuntimeError,match='permission denied'): db.execute('delete from student_visits')
    with pytest.raises(RuntimeError,match='permission denied'): db.execute('select public.piano_registration_time(%s)',(U,))
    db.execute("select set_config('request.jwt.claim.sub',%s,false)",(U,))
    assert len(db.execute('select * from student_attempts').fetchall())==1


def test_actual_context_query_and_finalize_keeps_multiple_logs(db):
    from pianokt_backend.pipeline.attempts import read_context,utc
    from pianokt_backend.upload_service import commit_finalization
    for i in (1,2):
        aid=f'20000000-0000-0000-0000-{i:012}'
        body=full_packet(aid)
        body.activity.events[0].event_id=UUID(f'50000000-0000-0000-0000-{i:012}')
        body.activity.events[1].event_id=UUID(f'60000000-0000-0000-0000-{i:012}')
        commit_finalization(db,new_upload(db,aid),U,body)
        assert db.execute('select count(*)::int n from user_play_logs where session_id=%s',(aid,)).fetchone()['n']==0
        from pianokt_backend.projections import rebuild_projections
        db.execute('update piano_attempts set validated_at=now() where id=%s',(aid,))
        rebuild_projections(db,aid)
    assert db.execute('select count(*)::int n from user_play_logs').fetchone()['n']==2
    assert float(db.execute('select days_since_signup from user_play_logs limit 1').fetchone()['days_since_signup'])>0
    context=read_context(db,utc('2099-01-01T00:00:00Z'))
    assert len(context['visits'])==1 and len(context['attempts'])==2 and len(context['events'])==4


def test_activity_transaction_rolls_back_every_write_on_conflict(db):
    save_activity(db,U,packet())
    bad=packet(10); bad.events[0].song_time_sec=999
    db.execute('begin')
    try:
        with pytest.raises(HTTPException,match='identity conflict'): save_activity(db,U,bad)
    finally: db.execute('rollback')
    assert db.execute('select last_sequence from student_visits').fetchone()['last_sequence']==1
    assert db.execute('select count(*)::int n from student_attempt_events').fetchone()['n']==1


def test_legacy_event_rpc_is_retired(db):
    db.execute('set role authenticated')
    with pytest.raises(RuntimeError,match='permission denied'):
        db.execute('select upsert_user_play_log(%s)',(A,))


def test_leaderboard_excludes_pending_and_client_scores(db):
    db.execute("insert into challenge_recordings(id,user_id,accuracy_pct,score_source,source_environment) values(%s,%s,100,'pending','production'),(%s,%s,40,'aligned','production'),(%s,%s,95,'legacy','unknown')",(A,U,V,U,OTHER,U))
    new_upload(db,A); new_upload(db,V)
    db.execute('update piano_attempts set validated_at=now()')
    row=db.execute("select * from get_leaderboard('accuracy')").fetchone()
    assert float(row['accuracy_avg'])==40 and row['challenge_count']==2


def test_finalize_is_atomic_and_immutable_on_retry(db):
    from pianokt_backend.upload_service import commit_finalization
    row=new_upload(db)
    body=full_packet()
    # Inject failure AFTER event hydration/sealing and upload status update.
    db.execute('alter table challenge_recordings add constraint injected_failure check(accuracy_pct < 0)')
    db.execute('begin')
    try:
        with pytest.raises(RuntimeError,match='injected_failure'): commit_finalization(db,row,U,body)
    finally: db.execute('rollback')
    assert db.execute('select status from piano_attempts').fetchone()['status']=='CREATED'
    for table in ('piano_outbox','challenge_recordings','play_events_raw','user_play_logs','student_attempt_events','student_attempts','student_visits'):
        assert db.execute(f'select count(*)::int n from {table}').fetchone()['n']==0
    db.execute('alter table challenge_recordings drop constraint injected_failure')
    db.execute('begin'); commit_finalization(db,row,U,body); db.execute('commit')
    db.execute('begin'); commit_finalization(db,row,U,body); db.execute('commit')
    assert db.execute("select count(*)::int n from piano_outbox where event_type='performance.submitted'").fetchone()['n']==1
    assert db.execute('select score_source from challenge_recordings').fetchone()['score_source']=='pending'
    assert db.execute('select count(*)::int n from play_events_raw').fetchone()['n']==0
    seal=db.execute('select sealed_at,sealed_event_count from student_attempts').fetchone()
    assert seal['sealed_at'] and seal['sealed_event_count']==2
    bad=body.model_copy(deep=True); bad.activity.events[-1].metadata={'tampered':True}
    with pytest.raises(HTTPException,match='identity conflict'): commit_finalization(db,row,U,bad)


def test_worker_fencing_and_completion_rollback(db):
    from pianokt_backend.worker import commit_alignment
    db.execute('insert into piano_learner_keys(user_id) values(%s)',(U,))
    db.execute("insert into piano_attempts(id,user_id,learner_key,song_id,song_source,performance_key,reference_key,performance_hash,reference_hash,status,lease_token,source_environment) values(%s,%s,(select learner_key from piano_learner_keys where user_id=%s),'song','local','p','r','h','h','PROCESSING',%s,'production')",(A,U,U,V))
    db.execute("insert into challenge_recordings(id,user_id,score_source) values(%s,%s,'pending')",(A,U))
    result={'alignment_run_id':'run','summary':{'accuracy':.8,'alignment_quality_valid':True}}
    with pytest.raises(RuntimeError,match='superseded'): commit_alignment(db,A,OTHER,'result.json',result)
    assert db.execute('select status from piano_attempts').fetchone()['status']=='PROCESSING'
    # Failure after the READY write must roll it back as well.
    db.execute("alter table challenge_recordings add constraint injected_failure check(accuracy_pct < 50)")
    db.execute('begin')
    try:
        with pytest.raises(RuntimeError,match='injected_failure'): commit_alignment(db,A,V,'result.json',result)
    finally: db.execute('rollback')
    assert db.execute('select status from piano_attempts').fetchone()['status']=='PROCESSING'
    assert db.execute('select result_digest from piano_attempts').fetchone()['result_digest'] is None
    assert db.execute('select count(*)::int n from piano_outbox').fetchone()['n']==0
    db.execute('alter table challenge_recordings drop constraint injected_failure')
    db.execute('begin'); commit_alignment(db,A,V,'result.json',result); db.execute('commit')
    assert db.execute('select status from piano_attempts').fetchone()['status']=='READY'
    assert float(db.execute('select accuracy_pct from challenge_recordings').fetchone()['accuracy_pct'])==80
    committed=db.execute('select result_digest,result_committed_at from piano_attempts').fetchone()
    assert len(committed['result_digest'])==64 and committed['result_committed_at']
    from pianokt_backend.pipeline.direct import read_committed
    source=read_committed(db,'2100-01-01T00:00:00Z')
    assert len(source.rows)==1 and str(source.rows[0]['attempt_id'])==A
    assert read_committed(db,'2000-01-01T00:00:00Z').rows==()


def test_legacy_atomic_rpc_rejects_new_writes(db):
    db.execute("select set_config('request.jwt.claim.sub',%s,false)",(U,))
    db.execute('set role authenticated')
    with pytest.raises(RuntimeError,match='permission denied'):
        db.execute("select log_play_event_atomic(%s,%s,'song',null,'freeplay','finished',1,'2026-01-01T00:00:01Z','{}')",(V,A))


@pytest.mark.parametrize('digits', [0, 1])
@pytest.mark.parametrize('start_time', [0.12345678901234567, 0.00770000000018626])
def test_long_decimal_activity_and_finalize_are_idempotent(db, digits, start_time):
    from pianokt_backend.upload_service import commit_finalization
    db.execute(f'set extra_float_digits = {digits}')
    body=full_packet()
    body.activity.events[0].song_time_sec=start_time
    body.activity.events[0].elapsed_playing_sec=0.9876543210987654
    body.activity.events[-1].song_time_sec=1.2345678901234567
    body.activity.events[-1].elapsed_playing_sec=1.9876543210987654
    save_activity(db,U,body.activity)
    row=new_upload(db)
    commit_finalization(db,row,U,body)
    commit_finalization(db,row,U,body)
    save_activity(db,U,body.activity)
    assert db.execute('select count(*)::int n from student_attempt_events').fetchone()['n']==2
    assert db.execute('select count(*)::int n from play_events_raw').fetchone()['n']==0
    import math
    for field in ('song_time_sec','elapsed_playing_sec'):
        changed=body.activity.model_copy(deep=True)
        setattr(changed.events[0],field,math.nextafter(getattr(changed.events[0],field),math.inf))
        with pytest.raises(HTTPException,match='Activity event identity conflict'): save_activity(db,U,changed)


def full_packet(aid=A):
    from pianokt_backend.upload_contracts import Finalize
    body=packet(attempt=aid)
    body.events.append(packet(2,event_sequence=1,event_type='finished',attempt=aid).events[0])
    body.events[-1].metadata={'success':True}
    return Finalize(schema_version=3,activity=body)


def new_upload(db,aid=A):
    from pianokt_backend.provenance import recording_environment
    db.execute('insert into piano_learner_keys(user_id) values(%s) on conflict do nothing',(U,))
    return db.execute("""insert into piano_attempts(id,user_id,learner_key,song_id,song_source,metadata,performance_key,reference_key,performance_hash,reference_hash,source_environment)
        values(%s,%s,(select learner_key from piano_learner_keys where user_id=%s),'song','local',%s,'p','r','h','h',%s) returning *""",
        (aid,U,U,json.dumps({'schema_version':2,'duration_sec':10,'settings':{'client_accuracy_pct':99}}),recording_environment())).fetchone()


@pytest.mark.parametrize('delivery', ['none','start','end','reverse'])
def test_finalize_fills_missing_activity_and_accepts_late_duplicates(db,delivery):
    from pianokt_backend.upload_service import commit_finalization
    body=full_packet()
    for i in {'none':[], 'start':[0], 'end':[1], 'reverse':[1,0]}[delivery]:
        part=body.activity.model_copy(deep=True); part.events=[part.events[i]]
        save_activity(db,U,part)
    commit_finalization(db,new_upload(db),U,body)
    save_activity(db,U,body.activity)
    assert db.execute('select count(*)::int n from student_attempt_events').fetchone()['n']==2
    extra=packet(3,event_sequence=2,event_type='interrupted')
    with pytest.raises(HTTPException,match='immutable'): save_activity(db,U,extra)


@pytest.mark.parametrize('sequence',[1,2])
def test_interruption_is_never_overwritten_by_finalize(db,sequence):
    from pianokt_backend.upload_service import commit_finalization
    save_activity(db,U,packet())
    save_activity(db,U,packet(3,event_sequence=sequence,event_type='interrupted'))
    row=new_upload(db)
    db.execute('begin')
    try:
        with pytest.raises(HTTPException): commit_finalization(db,row,U,full_packet())
    finally: db.execute('rollback')
    assert db.execute('select status from piano_attempts').fetchone()['status']=='CREATED'
    assert db.execute("select count(*)::int n from student_attempt_events where event_type='interrupted'").fetchone()['n']==1


def test_environment_cannot_be_rebound_and_exports_filter_context(db,monkeypatch):
    from pianokt_backend.pipeline.attempts import read_context
    save_activity(db,U,packet())
    monkeypatch.setenv('PIANOKT_ENV','development')
    with pytest.raises(HTTPException,match='Visit identity'): save_activity(db,U,packet())
    body=packet(attempt=OTHER); body.visit.id=UUID(OTHER); body.events[0].event_id=UUID(OTHER)
    save_activity(db,U,body)
    production=read_context(db,'2100-01-01T00:00:00Z')
    assert len(production['visits'])==len(production['attempts'])==len(production['events'])==1
    dev=read_context(db,'2100-01-01T00:00:00Z','development')
    assert str(dev['attempts'][0]['attempt_id'])==OTHER


def test_projection_rebuild_preserves_client_outcome_separate_from_grade(db):
    from pianokt_backend.upload_service import commit_finalization
    from pianokt_backend.projections import rebuild_projections
    commit_finalization(db,new_upload(db),U,full_packet())
    db.execute("update piano_attempts set status='READY',validated_at=now(),summary='{\"alignment_quality_valid\":true,\"accuracy\":0.4}'")
    db.execute('delete from user_play_logs')
    db.execute('delete from challenge_recordings')
    rebuild_projections(db,A)
    rebuild_projections(db,A)
    assert float(db.execute('select accuracy_pct from challenge_recordings').fetchone()['accuracy_pct'])==40
    log=db.execute('select completion_reason,client_success,exit_status,projection_version from user_play_logs').fetchone()
    assert log==dict(completion_reason='finished',client_success=True,exit_status='succeeded',projection_version=2)
    assert db.execute('select count(*)::int n from piano_outbox').fetchone()['n']==1


def test_http_v2_upload_finalize_without_live_activity_and_digest_retry(db,monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from pianokt_backend import api
    from pianokt_backend.contracts import digest
    @contextmanager
    def connect():
        db.execute('begin')
        try:
            yield db
            db.execute('commit')
        except Exception:
            db.execute('rollback')
            raise
    monkeypatch.setattr(api,'connect',connect)
    monkeypatch.setattr(api,'signed',lambda key,*args:'https://fixture.invalid/'+key)
    midi=b'MThd'+b'\x00'*20
    class Blob:
        generation=1
        data=midi
        @property
        def size(self): return len(self.data)
        def reload(self): pass
        def download_as_bytes(self,**kwargs): return self.data
    blob=Blob()
    monkeypatch.setattr(api,'objects',lambda:SimpleNamespace(bucket=SimpleNamespace(blob=lambda key:blob),_name=lambda key:key))
    api.app.dependency_overrides[api.user]=lambda:U
    try:
        client=TestClient(api.app)
        upload=dict(schema_version=2,attempt_id=A,song_id='song',song_source='local',
                    performance_hash=digest(midi),reference_hash=digest(midi),duration_sec=10)
        assert client.post('/attempts',json=upload).status_code==200
        body=full_packet().model_dump(mode='json')
        monkeypatch.setattr(api, 'objects', lambda: pytest.fail('async finalize must not access GCS'))
        old={**body,'schema_version':2}
        assert client.post(f'/attempts/{A}/finalize',json=old).status_code==422
        assert client.post(f'/attempts/{A}/finalize',json=body).status_code==202
        assert db.execute('select status from piano_attempts').fetchone()['status']=='ACCEPTED'
        assert client.post(f'/attempts/{A}/finalize',json=body).status_code==202
        assert client.post('/activity',json=body['activity']).status_code==200
        assert db.execute('select count(*)::int n from play_events_raw').fetchone()['n']==0
        body['activity']['events'][-1]['elapsed_playing_sec']=2
        assert client.post(f'/attempts/{A}/finalize',json=body).status_code==409
    finally:
        api.app.dependency_overrides.clear()
