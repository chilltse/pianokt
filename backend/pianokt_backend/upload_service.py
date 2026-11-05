"""Atomic Challenge v2 commit. Caller owns the database transaction."""
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from .activity import save_activity
from .projections import project_challenge
from .provenance import recording_environment
from .upload_contracts import validate_finalization


def commit_finalization(c, row, uid, body):
    attempt_id = row['id']
    c.execute('select pg_advisory_xact_lock(hashtext(%s))', (str(attempt_id),))
    locked = c.execute('select * from public.piano_attempts where id=%s and user_id=%s for update', (attempt_id, uid)).fetchone()
    if not locked:
        raise HTTPException(404, 'Attempt not found')
    if locked['source_environment'] != recording_environment():
        raise HTTPException(409, 'Attempt environment conflict')
    canonical = body.model_dump(mode='json')
    if locked.get('finalization') is not None:
        if locked['finalization'] != canonical:
            raise HTTPException(409, 'Finalization identity conflict')
        return
    if locked['status'] != 'CREATED':
        raise HTTPException(409, 'Attempt lacks a valid finalization')
    events = validate_finalization(locked, body, uid)
    # The same writer handles live, delayed and final packets. Its attempt lock
    # is reentrant inside this transaction; no second event authority is written.
    save_activity(c, uid, body.activity)
    count = c.execute('select count(*) as n from public.student_attempt_events where attempt_id=%s', (attempt_id,)).fetchone()['n']
    if int(count) != len(events):
        raise HTTPException(409, 'Finalized cycle omits recorded activity')
    c.execute('update public.student_attempts set sealed_at=clock_timestamp(),sealed_event_count=%s where attempt_id=%s', (len(events), attempt_id))
    c.execute("update public.piano_attempts set status='UPLOADED',finalization=%s,updated_at=now() where id=%s", (Jsonb(canonical), attempt_id))
    project_challenge(c, locked, body)
    payload = dict(event_type='performance.uploaded', attempt_id=str(attempt_id),
                   learner_key=str(locked['learner_key']), source_environment=locked['source_environment'])
    c.execute('insert into public.piano_outbox(event_type,aggregate_id,payload) values(%s,%s,%s) on conflict do nothing',
              ('performance.uploaded', str(attempt_id), Jsonb(payload)))
