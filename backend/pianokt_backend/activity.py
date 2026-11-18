"""Durable visit/attempt history, independent of MIDI finalization."""
from .timing import stage, sql, event_count
import json
import logging
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, Field, model_validator, ConfigDict
from psycopg.types.json import Jsonb
from .provenance import recording_environment


class Visit(BaseModel):
    id: UUID
    started_at: AwareDatetime
    occurred_at: AwareDatetime
    sequence: int = Field(ge=0)
    state: Literal['active', 'pagehide', 'signed_out'] = 'active'


class AttemptContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: UUID
    visit_attempt_sequence: int = Field(gt=0)
    song_id: str = Field(min_length=1, max_length=300)
    song_source: Literal['builtin','local','generated','base64']
    started_at: AwareDatetime
    segment_id: UUID | None = None
    segment_index: int | None = Field(default=None,ge=1)
    midi_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def validate_catalog_identity(self):
        from .song_identity import validate_identity
        self.song_id = validate_identity(self.song_source,self.song_id,self.segment_id,self.segment_index,self.midi_sha256)
        return self


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
        with stage('db.attempt_lock'):
            sql(c, 'advisory.activity', 'select pg_advisory_xact_lock(hashtext(%s))',(str(body.attempt.attempt_id),))
    return _save_activity_locked(c, uid, body)


def _save_activity_locked(c, uid, body):
    """Internal writer: caller holds this Attempt's advisory xact lock.

    finalize validates that body.attempt matches its locked piano_attempt first.
    Visit-only packets need no Attempt lock. The caller owns the transaction.
    """
    if str(body.owner_id) != str(uid):
        raise HTTPException(403, 'Activity belongs to another signed-in user')
    event_count('activity.events', len(body.events))
    environment = recording_environment()
    v = body.visit
    sql(c, 'learner.write', 'insert into public.piano_learner_keys(user_id) values(%s) on conflict do nothing', (uid,))
    if body.events and body.attempt is None:
        raise HTTPException(422, 'Events require attempt context')
    sql(c, 'visit.insert', '''insert into public.student_visits(id,user_id,started_at,last_seen_at,last_sequence,source_environment)
        values(%s,%s,%s,%s,0,%s) on conflict(id) do nothing''', (v.id, uid, v.started_at, v.occurred_at, environment))
    row = sql(c, 'row_lock.visit', 'select * from public.student_visits where id=%s for update', (v.id,)).fetchone()
    if not row or str(row['user_id']) != str(uid) or row['started_at'] != v.started_at or row['source_environment'] != environment:
        raise HTTPException(409, 'Visit identity conflict')
    sql(c, 'visit.update', '''update public.student_visits set last_seen_at=%s,last_sequence=%s,
        ended_at=%s,end_reason=%s,end_is_inferred=%s where id=%s and last_sequence<=%s''',
        (v.occurred_at, v.sequence, None if v.state == 'active' else v.occurred_at,
         None if v.state == 'active' else v.state, v.state == 'pagehide', v.id, v.sequence))
    a = body.attempt
    if a is None:
        return
    from .song_identity import check_catalog_identity
    inserted = sql(c, 'attempt.insert', '''insert into public.student_attempts(attempt_id,user_id,visit_id,visit_attempt_sequence,song_id,song_source,started_at,source_environment,segment_id,segment_index,midi_sha256)
        values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing returning attempt_id''',
        (a.attempt_id, uid, v.id, a.visit_attempt_sequence, a.song_id, a.song_source, a.started_at, environment, a.segment_id, a.segment_index, a.midi_sha256)).fetchone()
    if inserted:
        check_catalog_identity(c, a)
    row = sql(c, 'row_lock.activity_attempt', 'select * from public.student_attempts where attempt_id=%s for update', (a.attempt_id,)).fetchone()
    expected = (str(uid), str(v.id), a.visit_attempt_sequence, a.song_id, a.song_source, a.started_at, environment)
    if not row or (str(row['user_id']), str(row['visit_id']), row['visit_attempt_sequence'], row['song_id'], row['song_source'], row['started_at'], row['source_environment']) != expected:
        raise HTTPException(409, 'Attempt context identity conflict')
    fields = ('segment_id','segment_index','midi_sha256')
    for field in fields:
        if str(row.get(field)) != str(getattr(a,field)):
            raise HTTPException(409,'Attempt MIDI/segment identity conflict')
    if _can_batch_events(body.events, a, row['sealed_at']):
        _save_events_batch(c, uid, a, body.events)
    else:
        # Keep sequential error precedence for sealed, malformed or internally
        # duplicated packets. Finalize's validated new cycles use the batch path.
        _save_events_sequential(c, uid, a, body.events, row['sealed_at'])
    # Out-of-order delivery is allowed; the latest sequence determines state.
    last = sql(c, 'event.latest_select', 'select event_type,occurred_at from public.student_attempt_events where attempt_id=%s order by event_sequence desc limit 1', (a.attempt_id,)).fetchone()
    if last:
        terminal = last['event_type'] in ('finished', 'exited', 'interrupted')
        sql(c, 'attempt.update', 'update public.student_attempts set ended_at=%s,end_reason=%s where attempt_id=%s',
                  (last['occurred_at'] if terminal else None, last['event_type'] if terminal else None, a.attempt_id))


def _can_batch_events(events, attempt, sealed_at):
    if not events or sealed_at is not None:
        return False
    if len({e.event_id for e in events}) != len(events) or len({e.event_sequence for e in events}) != len(events):
        return False
    return all(
        (e.event_sequence == 0) == (e.event_type == 'play_started')
        and (e.event_sequence != 0 or e.occurred_at == attempt.started_at)
        and e.event_sequence <= 2147483647
        and _batch_metadata_safe(e.metadata)
        for e in events
    )


def _batch_metadata_safe(metadata):
    # Unsupported JSONB values must reach the original sequential path, so a
    # later database/adaptation error cannot precede an earlier identity error.
    try:
        encoded = json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return False
    return len(encoded) <= 16000 and r'\u0000' not in encoded and r'\ud' not in encoded


def _save_events_batch(c, uid, a, events):
    # Only placeholder structure is generated; every value remains bound.
    values = ','.join(['(%s,%s,%s,%s,%s,%s,%s,%s,%s)'] * len(events))
    parameters = tuple(value for e in events for value in (
        e.event_id, a.attempt_id, uid, e.event_sequence, e.event_type,
        e.occurred_at, e.song_time_sec, e.elapsed_playing_sec, Jsonb(e.metadata)))
    sql(c, 'event.batch_insert',
        'insert into public.student_attempt_events(event_id,attempt_id,user_id,event_sequence,event_type,occurred_at,song_time_sec,elapsed_playing_sec,metadata) values '
        + values + ' on conflict do nothing', parameters)
    # Separate statement: see rows committed by a competing unique-key writer
    # that the INSERT waited for. A data-modifying CTE shares one snapshot and
    # cannot replace this check safely. LEFT JOIN retains missing/conflicting IDs.
    inputs = ','.join(f'({i},%s::uuid,%s::double precision,%s::double precision)' for i in range(len(events)))
    parameters = tuple(value for e in events for value in (e.event_id, e.song_time_sec, e.elapsed_playing_sec))
    stored = sql(c, 'event.batch_conflict_select',
        'select stored.*, input.ordinal, '
        'stored.song_time_sec is not distinct from input.song_time_sec as song_time_matches, '
        'stored.elapsed_playing_sec is not distinct from input.elapsed_playing_sec as elapsed_matches '
        'from (values ' + inputs + ') as input(ordinal,event_id,song_time_sec,elapsed_playing_sec) '
        'left join public.student_attempt_events stored on stored.event_id=input.event_id '
        'order by input.ordinal', parameters).fetchall()
    for e, row in zip(events, stored, strict=True):
        _check_event_identity(a, e, row if row['event_id'] is not None else None)


def _save_events_sequential(c, uid, a, events, sealed_at):
    for e in events:
        if sealed_at is not None:
            existing = sql(c, 'event.sealed_select', 'select event_id from public.student_attempt_events where attempt_id=%s and event_id=%s',
                                 (a.attempt_id, e.event_id)).fetchone()
            if not existing:
                raise HTTPException(409, 'Finalized activity is immutable')
        if (e.event_sequence == 0) != (e.event_type == 'play_started'):
            raise HTTPException(422, 'Sequence zero must be the unique play_started event')
        if e.event_sequence == 0 and e.occurred_at != a.started_at:
            raise HTTPException(422, 'Start event must match attempt started_at')
        if len(json.dumps(e.metadata)) > 16000:
            raise HTTPException(422, 'Event metadata too large')
        sql(c, 'event.insert', '''insert into public.student_attempt_events(event_id,attempt_id,user_id,event_sequence,event_type,occurred_at,song_time_sec,elapsed_playing_sec,metadata)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing''',
            (e.event_id, a.attempt_id, uid, e.event_sequence, e.event_type, e.occurred_at, e.song_time_sec, e.elapsed_playing_sec, Jsonb(e.metadata)))
        # Compare float8 values inside PostgreSQL: text output can be rounded by
        # extra_float_digits (including settings on pooled connections).
        stored = sql(c, 'event.conflict_select', '''select *,
            song_time_sec is not distinct from %s::double precision as song_time_matches,
            elapsed_playing_sec is not distinct from %s::double precision as elapsed_matches
            from public.student_attempt_events where event_id=%s''',
            (e.song_time_sec, e.elapsed_playing_sec, e.event_id)).fetchone()
        _check_event_identity(a, e, stored)


def _check_event_identity(a, e, stored):
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
