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
                if k.endswith('_at') and isinstance(v,str): row[k]=datetime.fromisoformat(v.replace('Z','+00:00'))
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
