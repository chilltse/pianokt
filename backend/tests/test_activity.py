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
        def encode(v):
            if hasattr(v,'obj'): return v.obj
            if isinstance(v,datetime): return v.isoformat()
            return str(v)
        i=0
        while '%s' in sql:
            i+=1; sql=sql.replace('%s',f'${i}',1)
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
def db():
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
        attempt=dict(attempt_id=attempt,visit_attempt_sequence=1 if attempt==A else 2,song_id='song',song_source='builtin',started_at='2026-01-01T00:00:10Z'),
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
    from pianokt_backend.api import ChallengeCycle,commit_cycle,validate_cycle
    for i in (1,2):
        aid=f'20000000-0000-0000-0000-{i:012}'
        body=packet(i,attempt=aid)
        # Different IDs for every attempt; final upload and live stream must agree.
        body.events[0].event_id=UUID(f'50000000-0000-0000-0000-{i:012}')
        end=body.events[0].model_copy(update=dict(event_id=UUID(f'60000000-0000-0000-0000-{i:012}'),event_sequence=1,event_type='exited',occurred_at=utc('2026-01-01T00:00:11Z'),song_time_sec=1.,elapsed_playing_sec=1.))
        body.events.append(end)
        for event in body.events: event.metadata={'time_playing_sec':event.elapsed_playing_sec}
        save_activity(db,U,body)
        db.execute('insert into challenge_recordings(id,user_id) values(%s,%s)',(aid,U))
        meta={'session_id':aid,'duration_sec':10}
        db.execute('''insert into piano_attempts(id,user_id,learner_key,song_id,song_source,metadata,performance_key,reference_key,performance_hash,reference_hash)
          values(%s,%s,(select learner_key from piano_learner_keys where user_id=%s),'song','builtin',%s,'p','r','h','h')''',(aid,U,U,json.dumps(meta)))
        cycle=ChallengeCycle.model_validate(dict(session_id=aid,events=[dict(event_id=e.event_id,event_type=e.event_type,client_ts=e.occurred_at,song_time_sec=e.song_time_sec,metadata=e.metadata) for e in body.events]))
        row={'id':aid,'metadata':meta,'song_id':'song'}
        commit_cycle(db,row,U,cycle,validate_cycle(row,cycle))
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


def test_legacy_aggregation_uses_client_clock_and_finished_only(db):
    db.execute("insert into play_events_raw(event_id,session_id,user_id,song_id,play_mode,event_type,song_time_sec,client_ts,metadata) values(%s,%s,%s,'song','challenge','play_started',0,'2026-01-01T08:00:00+08:00','{}')",(V,A,U))
    db.execute("insert into play_events_raw(event_id,session_id,user_id,song_id,play_mode,event_type,song_time_sec,client_ts,metadata) values(%s,%s,%s,'song','challenge','exited',99,'2026-01-01T00:01:00Z','{\"song_duration_sec\":100}')",(A,A,U))
    db.execute("select set_config('request.jwt.claim.sub',%s,false)",(U,))
    db.execute('select upsert_user_play_log(%s)',(A,))
    row=db.execute('select * from user_play_logs').fetchone()
    assert row['started_at'].isoformat()=='2026-01-01T00:00:00+00:00'
    assert not row['is_played_in_full']


def test_leaderboard_excludes_pending_and_client_scores(db):
    db.execute("insert into challenge_recordings(id,user_id,accuracy_pct,score_source) values(%s,%s,100,'pending'),(%s,%s,40,'aligned'),(%s,%s,95,'legacy')",(A,U,V,U,OTHER,U))
    row=db.execute("select * from get_leaderboard('accuracy')").fetchone()
    assert float(row['accuracy_avg'])==40 and row['challenge_count']==3


def test_finalize_is_atomic_and_immutable_on_retry(db):
    from pianokt_backend.upload_contracts import Finalize,validate_cycle
    from pianokt_backend.upload_service import commit_finalization
    db.execute('insert into piano_learner_keys(user_id) values(%s)',(U,))
    meta={'session_id':A,'duration_sec':10,'settings':{'client_accuracy_pct':99}}
    row=db.execute('''insert into piano_attempts(id,user_id,learner_key,song_id,song_source,metadata,performance_key,reference_key,performance_hash,reference_hash)
      values(%s,%s,(select learner_key from piano_learner_keys where user_id=%s),'song','builtin',%s,'p','r','h','h') returning *''',(A,U,U,json.dumps(meta))).fetchone()
    body=Finalize.model_validate({'cycle':{'session_id':A,'events':[
        dict(event_id=V,event_type='play_started',song_time_sec=0,client_ts='2026-01-01T00:00:00Z'),
        dict(event_id=A,event_type='exited',song_time_sec=1,client_ts='2026-01-01T00:00:01Z'),
    ]}})
    timestamps=validate_cycle(row,body.cycle)
    # An error after the status/outbox/recording writes must roll them all back.
    bad=body.model_copy(deep=True); bad.cycle.events[-1].metadata={'time_playing_sec':-1}
    db.execute('begin')
    try:
        with pytest.raises(HTTPException,match='time_playing'): commit_finalization(db,row,U,bad,timestamps)
    finally: db.execute('rollback')
    assert db.execute('select status from piano_attempts').fetchone()['status']=='CREATED'
    for table in ('piano_outbox','challenge_recordings','play_events_raw','user_play_logs'):
        assert db.execute(f'select count(*)::int n from {table}').fetchone()['n']==0
    db.execute('begin'); commit_finalization(db,row,U,body,timestamps); db.execute('commit')
    db.execute('begin'); commit_finalization(db,row,U,body,timestamps); db.execute('commit')
    assert db.execute("select count(*)::int n from piano_outbox where event_type='performance.uploaded'").fetchone()['n']==1
    assert db.execute('select score_source from challenge_recordings').fetchone()['score_source']=='pending'
    with pytest.raises(HTTPException,match='identity conflict'): commit_finalization(db,row,U,bad,timestamps)


def test_worker_fencing_and_completion_rollback(db):
    from pianokt_backend.worker import commit_alignment
    db.execute('insert into piano_learner_keys(user_id) values(%s)',(U,))
    db.execute("insert into piano_attempts(id,user_id,learner_key,song_id,song_source,performance_key,reference_key,performance_hash,reference_hash,status,lease_token) values(%s,%s,(select learner_key from piano_learner_keys where user_id=%s),'song','builtin','p','r','h','h','PROCESSING',%s)",(A,U,U,V))
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


def test_legacy_atomic_rpc_retries_and_conflict_rolls_back(db):
    db.execute("select set_config('request.jwt.claim.sub',%s,false)",(U,))
    db.execute('set role authenticated')
    sql="select log_play_event_atomic(%s,%s,'song',null,'freeplay','finished',1,'2026-01-01T00:00:01Z','{}')"
    db.execute(sql,(V,A)); db.execute(sql,(V,A))
    assert db.execute('select count(*)::int n from play_events_raw').fetchone()['n']==1
    assert db.execute('select events_count from user_play_logs').fetchone()['events_count']==1
    with pytest.raises(RuntimeError,match='identity conflict'):
        db.execute("select log_play_event_atomic(%s,%s,'other',null,'freeplay','finished',1,'2026-01-01T00:00:01Z','{}')",(V,A))
    assert db.execute('select song_id from user_play_logs').fetchone()['song_id']=='song'
    with pytest.raises(RuntimeError,match='permission denied'):
        db.execute("select log_play_event(%s,%s,'song')",(OTHER,A))


@pytest.mark.parametrize('digits', [0, 1])
def test_long_decimal_activity_and_finalize_are_idempotent(db, digits):
    from pianokt_backend.upload_contracts import ChallengeCycle, commit_cycle, validate_cycle
    db.execute(f'set extra_float_digits = {digits}')
    body = packet()
    body.events[0].song_time_sec = 0.12345678901234567
    body.events[0].elapsed_playing_sec = 0.9876543210987654
    end = packet(2, event_sequence=1, event_type='finished').events[0]
    end.song_time_sec = 1.2345678901234567
    end.elapsed_playing_sec = 1.9876543210987654
    body.events.append(end)
    for event in body.events:
        event.metadata = {'time_playing_sec': event.elapsed_playing_sec}
    # Live activity, then the identical complete packet submitted by /attempts.
    save_activity(db, U, body)
    save_activity(db, U, body)
    db.execute('insert into challenge_recordings(id,user_id) values(%s,%s)', (A,U))
    cycle = ChallengeCycle.model_validate(dict(session_id=A, events=[
        dict(event_id=e.event_id, event_type=e.event_type, client_ts=e.occurred_at,
             song_time_sec=e.song_time_sec, metadata=e.metadata) for e in body.events
    ]))
    row = {'id': A, 'song_id': 'song', 'metadata': {'session_id': A, 'duration_sec': 10}}
    timestamps = validate_cycle(row, cycle)
    commit_cycle(db, row, U, cycle, timestamps)
    commit_cycle(db, row, U, cycle, timestamps)
    assert db.execute('select count(*)::int n from student_attempt_events').fetchone()['n'] == 2
    assert db.execute('select count(*)::int n from play_events_raw').fetchone()['n'] == 2
    # Even a one-ULP real change must remain a conflict; no rounding/tolerance workaround.
    import math
    for field in ('song_time_sec', 'elapsed_playing_sec'):
        changed = body.model_copy(deep=True)
        setattr(changed.events[0], field, math.nextafter(getattr(changed.events[0], field), math.inf))
        with pytest.raises(HTTPException, match='Activity event identity conflict'):
            save_activity(db, U, changed)
    changed_cycle = cycle.model_copy(deep=True)
    changed_cycle.events[0].song_time_sec = math.nextafter(cycle.events[0].song_time_sec, math.inf)
    with pytest.raises(HTTPException, match='Cycle event identity conflict'):
        commit_cycle(db, row, U, changed_cycle, timestamps)
