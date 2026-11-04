"""Durable visit/attempt history, independent of MIDI finalization."""
import json
import logging
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, Field
from psycopg.types.json import Jsonb


class Visit(BaseModel):
    id: UUID
    started_at: AwareDatetime
    occurred_at: AwareDatetime
    sequence: int = Field(ge=0)
    state: Literal['active', 'pagehide', 'signed_out'] = 'active'


class AttemptContext(BaseModel):
    attempt_id: UUID
    visit_attempt_sequence: int = Field(gt=0)
    song_id: str = Field(min_length=1, max_length=300)
    song_source: str = Field(max_length=50)
    started_at: AwareDatetime


class ActivityEvent(BaseModel):
    event_id: UUID
    event_sequence: int = Field(ge=0)
    event_type: Literal['play_started', 'paused', 'resumed', 'finished', 'exited', 'interrupted']
    occurred_at: AwareDatetime
    song_time_sec: float = Field(ge=0, le=14400, allow_inf_nan=False)
    elapsed_playing_sec: float = Field(ge=0, le=86400, allow_inf_nan=False)
    metadata: dict = Field(default_factory=dict)


class Activity(BaseModel):
    owner_id: UUID
    visit: Visit
    attempt: AttemptContext | None = None
    events: list[ActivityEvent] = Field(default_factory=list, max_length=1000)


def save_activity(c, uid, body):
    """Idempotent events; sequence guards against delayed heartbeats/close packets."""
    if str(body.owner_id) != str(uid):
        raise HTTPException(403, 'Activity belongs to another signed-in user')
    if body.attempt is not None:
        c.execute('select pg_advisory_xact_lock(hashtext(%s))',(str(body.attempt.attempt_id),))
    v = body.visit
    c.execute('insert into public.piano_learner_keys(user_id) values(%s) on conflict do nothing', (uid,))
    if body.events and body.attempt is None:
        raise HTTPException(422, 'Events require attempt context')
    c.execute('''insert into public.student_visits(id,user_id,started_at,last_seen_at,last_sequence)
        values(%s,%s,%s,%s,0) on conflict(id) do nothing''', (v.id, uid, v.started_at, v.occurred_at))
    row = c.execute('select * from public.student_visits where id=%s for update', (v.id,)).fetchone()
    if not row or str(row['user_id']) != str(uid) or row['started_at'] != v.started_at:
        raise HTTPException(409, 'Visit identity conflict')
    c.execute('''update public.student_visits set last_seen_at=%s,last_sequence=%s,
        ended_at=%s,end_reason=%s,end_is_inferred=%s where id=%s and last_sequence<=%s''',
        (v.occurred_at, v.sequence, None if v.state == 'active' else v.occurred_at,
         None if v.state == 'active' else v.state, v.state == 'pagehide', v.id, v.sequence))
    a = body.attempt
    if a is None:
        return
    c.execute('''insert into public.student_attempts(attempt_id,user_id,visit_id,visit_attempt_sequence,song_id,song_source,started_at)
        values(%s,%s,%s,%s,%s,%s,%s) on conflict do nothing''',
        (a.attempt_id, uid, v.id, a.visit_attempt_sequence, a.song_id, a.song_source, a.started_at))
    row = c.execute('select * from public.student_attempts where attempt_id=%s for update', (a.attempt_id,)).fetchone()
    expected = (str(uid), str(v.id), a.visit_attempt_sequence, a.song_id, a.song_source, a.started_at)
    if not row or (str(row['user_id']), str(row['visit_id']), row['visit_attempt_sequence'], row['song_id'], row['song_source'], row['started_at']) != expected:
        raise HTTPException(409, 'Attempt context identity conflict')
    sealed=c.execute('select finalization from public.piano_attempts where id=%s',(a.attempt_id,)).fetchone()
    sealed_ids={e['event_id'] for e in sealed['finalization']['cycle']['events']} if sealed and sealed.get('finalization') else None
    for e in body.events:
        if sealed_ids is not None and str(e.event_id) not in sealed_ids:
            raise HTTPException(409, 'Finalized activity is immutable')
        if (e.event_sequence == 0) != (e.event_type == 'play_started'):
            raise HTTPException(422, 'Sequence zero must be the unique play_started event')
        if e.event_sequence == 0 and e.occurred_at != a.started_at:
            raise HTTPException(422, 'Start event must match attempt started_at')
        if len(json.dumps(e.metadata)) > 16000:
            raise HTTPException(422, 'Event metadata too large')
        c.execute('''insert into public.student_attempt_events(event_id,attempt_id,user_id,event_sequence,event_type,occurred_at,song_time_sec,elapsed_playing_sec,metadata)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing''',
            (e.event_id, a.attempt_id, uid, e.event_sequence, e.event_type, e.occurred_at, e.song_time_sec, e.elapsed_playing_sec, Jsonb(e.metadata)))
        # Compare float8 values inside PostgreSQL: text output can be rounded by
        # extra_float_digits (including settings on pooled connections).
        stored = c.execute('''select *,
            song_time_sec is not distinct from %s::double precision as song_time_matches,
            elapsed_playing_sec is not distinct from %s::double precision as elapsed_matches
            from public.student_attempt_events where event_id=%s''',
            (e.song_time_sec, e.elapsed_playing_sec, e.event_id)).fetchone()
        differences = ['event_id_or_sequence'] if not stored else [
            k for k, value in e.model_dump().items()
            if k not in ('event_id', 'song_time_sec', 'elapsed_playing_sec') and stored[k] != value
        ]
        if stored:
            if str(stored['attempt_id']) != str(a.attempt_id): differences.append('attempt_id')
            if not stored['song_time_matches']: differences.append('song_time_sec')
            if not stored['elapsed_matches']: differences.append('elapsed_playing_sec')
        if differences:
            logging.getLogger(__name__).warning(
                'Activity identity conflict attempt=%s event=%s fields=%s',
                a.attempt_id, e.event_id, ','.join(differences))
            raise HTTPException(409, 'Activity event identity conflict')
    # Out-of-order delivery is allowed; the latest sequence determines state.
    last = c.execute('select event_type,occurred_at from public.student_attempt_events where attempt_id=%s order by event_sequence desc limit 1', (a.attempt_id,)).fetchone()
    if last:
        terminal = last['event_type'] in ('finished', 'exited', 'interrupted')
        c.execute('update public.student_attempts set ended_at=%s,end_reason=%s where attempt_id=%s',
                  (last['occurred_at'] if terminal else None, last['event_type'] if terminal else None, a.attempt_id))
