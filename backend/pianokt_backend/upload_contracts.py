"""Upload contracts and transactional cycle finalization shared by API and replay tests."""
import json
import math
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from pydantic import BaseModel, Field, AwareDatetime
from psycopg.types.json import Jsonb
from .activity import Activity
import os

class Upload(BaseModel):
    attempt_id:uuid.UUID
    song_id:str=Field(min_length=1,max_length=300)
    song_source:str=Field(max_length=50)
    song_title:str|None=Field(default=None,max_length=300)
    performance_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    reference_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    duration_sec:float=Field(gt=0,le=14400)
    session_id:uuid.UUID|None=None
    activity:Activity|None=None
    settings:dict=Field(default_factory=dict)
    performance_events_hash:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    performance_events_count:int|None=Field(default=None,ge=0,le=50000)

class CycleEvent(BaseModel):
    event_id:uuid.UUID
    event_type:str=Field(pattern='^(play_started|paused|resumed|finished|exited)$')
    song_time_sec:float=Field(ge=0,le=14400)
    client_ts:AwareDatetime
    metadata:dict=Field(default_factory=dict)

class ChallengeCycle(BaseModel):
    session_id:uuid.UUID
    events:list[CycleEvent]=Field(min_length=2,max_length=1000)

class Finalize(BaseModel):
    cycle:ChallengeCycle

class LocalAlignment(BaseModel):
    performance_midi_base64:str=Field(min_length=1,max_length=12_000_000)
    reference_midi_base64:str=Field(min_length=1,max_length=12_000_000)
    performance_events:dict|None=None
    observed_end_sec:float=Field(ge=0,le=14400)
    reference_origin_sec:float=Field(ge=0,le=14400)

def finite_number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def local_alignment_lab_enabled():
    """Strictly local preflight: never enabled in Cloud Run or by default."""
    return os.getenv('PIANOKT_ENV')=='development' and os.getenv('PIANOKT_LOCAL_ALIGNMENT_LAB')=='1' and not os.getenv('K_SERVICE')

def validate_alignment_window_settings(settings):
    """Reject contradictory snapshot-window metadata before creating an attempt.

    The reference MIDI is cropped into this window, so silently accepting an
    end before its origin would make a malformed attempt look like an empty
    (and potentially valid) score. Older clients may omit these optional
    fields; once present they must form a coherent window.
    """
    def range_value(name):
        value=settings.get(name)
        if value is None:
            return None
        if not isinstance(value,dict):
            raise HTTPException(422,f'Invalid {name}')
        start=value.get('start')
        end=value.get('end')
        if not finite_number(start) or not finite_number(end) or start<0 or end<start:
            raise HTTPException(422,f'Invalid {name}')
        return float(start),float(end)

    compatibility_range=range_value('range')
    observed_range=range_value('observed_range')
    assigned_range=range_value('assigned_range')
    observed=observed_range or compatibility_range
    origin_value=settings.get('reference_time_origin_sec')
    if origin_value is not None and (not finite_number(origin_value) or origin_value<0):
        raise HTTPException(422,'Invalid reference_time_origin_sec')
    origin=float(origin_value) if origin_value is not None else (observed[0] if observed else None)
    if observed and origin is not None and abs(observed[0]-origin)>1e-6:
        raise HTTPException(422,'Reference origin does not match observed range')
    if observed and assigned_range and (
        observed[0] < assigned_range[0] or observed[1] > assigned_range[1]
    ):
        raise HTTPException(422,'Observed range is outside assigned range')
    played_until=settings.get('played_until_sec')
    if played_until is not None:
        if not finite_number(played_until) or played_until<0 or played_until>14400:
            raise HTTPException(422,'Invalid played_until_sec')
        if origin is not None and played_until < origin:
            raise HTTPException(422,'Played-until time precedes reference origin')

def validate_performance_events(data,expected_count):
    """Validate the immutable browser telemetry sidecar before it becomes visible.

    These events are evidence, so validation deliberately checks shape and bounds
    without rewriting event order or trying to "fix" a client clock anomaly.
    """
    if not isinstance(data,dict) or data.get('schemaVersion')!=1:
        raise HTTPException(422,'Invalid performance event telemetry')
    events=data.get('events')
    segments=data.get('transportSegments')
    if not isinstance(events,list) or not isinstance(segments,list):
        raise HTTPException(422,'Invalid performance event telemetry')
    if len(events)>50000 or len(segments)>1000 or len(events)!=expected_count:
        raise HTTPException(422,'Invalid performance event telemetry count')
    previous_sequence=-1
    for event in events:
        if not isinstance(event,dict): raise HTTPException(422,'Invalid performance event')
        sequence=event.get('sequence')
        if not isinstance(sequence,int) or isinstance(sequence,bool) or sequence<=previous_sequence:
            raise HTTPException(422,'Performance event sequence is not increasing')
        previous_sequence=sequence
        if event.get('type') not in ('note_on','note_off'):
            raise HTTPException(422,'Invalid performance event type')
        for key in ('midiEventTimestamp','playerSongTime','performanceNowTimestamp'):
            if not finite_number(event.get(key)) or event[key]<0 or event[key]>86_400_000:
                raise HTTPException(422,f'Invalid performance event {key}')
        if not isinstance(event.get('pitch'),int) or isinstance(event['pitch'],bool) or not 0<=event['pitch']<=127:
            raise HTTPException(422,'Invalid performance event pitch')
        if not finite_number(event.get('velocity')) or not 0<=event['velocity']<=127:
            raise HTTPException(422,'Invalid performance event velocity')
        if event.get('timestampSource') not in ('web_midi','browser_keyboard'):
            raise HTTPException(422,'Invalid performance event timestamp source')
    previous_segment=-1
    previous_segment_song_time=None
    previous_segment_now=None
    transport_state='before_start'
    for segment in segments:
        if not isinstance(segment,dict): raise HTTPException(422,'Invalid transport segment')
        sequence=segment.get('sequence')
        if not isinstance(sequence,int) or isinstance(sequence,bool) or sequence<=previous_segment:
            raise HTTPException(422,'Transport segment sequence is not increasing')
        previous_segment=sequence
        if segment.get('type') not in ('start','resume','pause','stop'):
            raise HTTPException(422,'Invalid transport segment type')
        for key in ('playerSongTime','performanceNowTimestamp'):
            if not finite_number(segment.get(key)) or segment[key]<0 or segment[key]>86_400_000:
                raise HTTPException(422,f'Invalid transport segment {key}')
        if (
            previous_segment_song_time is not None
            and segment['playerSongTime'] < previous_segment_song_time
        ):
            raise HTTPException(422,'Transport segment song time is not monotonic')
        if (
            previous_segment_now is not None
            and segment['performanceNowTimestamp'] < previous_segment_now
        ):
            raise HTTPException(422,'Transport segment browser time is not monotonic')
        segment_type=segment['type']
        if segment_type=='start':
            if transport_state!='before_start':
                raise HTTPException(422,'Transport cycle has an invalid start')
            transport_state='active'
        elif segment_type=='pause':
            if transport_state!='active':
                raise HTTPException(422,'Transport cycle has an invalid pause')
            transport_state='paused'
        elif segment_type=='resume':
            if transport_state!='paused':
                raise HTTPException(422,'Transport cycle has an invalid resume')
            transport_state='active'
        else:  # stop
            if transport_state not in ('active','paused'):
                raise HTTPException(422,'Transport cycle has an invalid stop')
            transport_state='stopped'
        previous_segment_song_time=segment['playerSongTime']
        previous_segment_now=segment['performanceNowTimestamp']
    if transport_state!='stopped':
        raise HTTPException(422,'Transport cycle must start and end with stop')
    return data

def validate_cycle(row,cycle):
    session_id=row['metadata'].get('session_id')
    if not session_id or str(cycle.session_id)!=str(session_id):
        raise HTTPException(409,'Session does not match attempt')
    event_types=[event.event_type for event in cycle.events]
    if event_types[0]!='play_started' or event_types[-1] not in ('finished','exited'):
        raise HTTPException(422,'Cycle must start with play_started and end with finished or exited')
    if any(event_type in ('play_started','finished','exited') for event_type in event_types[1:-1]):
        raise HTTPException(422,'Cycle contains an invalid terminal or duplicate start event')
    if len({event.event_id for event in cycle.events})!=len(cycle.events):
        raise HTTPException(422,'Cycle event IDs must be unique')
    state='active'
    for event in cycle.events[1:]:
        if event.event_type=='paused':
            if state!='active': raise HTTPException(422,'Pause requires active playback')
            state='paused'
        elif event.event_type=='resumed':
            if state!='paused': raise HTTPException(422,'Resume requires paused playback')
            state='active'
    if any(a.song_time_sec>b.song_time_sec for a,b in zip(cycle.events,cycle.events[1:])):
        raise HTTPException(422,'Cycle song position is not monotonic')
    timestamps=[]
    for event in cycle.events:
        timestamp=event.client_ts
        if timestamp.tzinfo is None: timestamp=timestamp.replace(tzinfo=timezone.utc)
        timestamps.append(timestamp)
        if len(json.dumps(event.metadata))>16000: raise HTTPException(422,'Event metadata too large')
    if timestamps!=sorted(timestamps): raise HTTPException(422,'Cycle events are not chronological')
    return timestamps

def commit_cycle(c,row,uid,cycle,timestamps):
    """Write the user-visible challenge cycle inside the finalize transaction."""
    c.execute('select pg_advisory_xact_lock(hashtext(%s))',(str(cycle.session_id),))
    existing=c.execute('select user_id,challenge_recording_id from public.user_play_logs where session_id=%s for update',(cycle.session_id,)).fetchone()
    if existing and str(existing['user_id'])!=str(uid): raise HTTPException(409,'Cycle ownership conflict')
    if existing and existing['challenge_recording_id'] and str(existing['challenge_recording_id'])!=str(row['id']): raise HTTPException(409,'Cycle already belongs to another recording')
    for event in cycle.events:
        c.execute('''insert into public.play_events_raw(event_id,session_id,user_id,song_id,exercise_id,play_mode,event_type,song_time_sec,client_ts,metadata)
            values(%s,%s,%s,%s,%s,'challenge',%s,%s,%s,%s) on conflict(event_id) do nothing''',
            (event.event_id,cycle.session_id,uid,row['song_id'],f"challenge:{row['song_id']}",event.event_type,event.song_time_sec,event.client_ts,Jsonb(event.metadata)))
        saved=c.execute('select *, song_time_sec is not distinct from %s::double precision as song_time_matches from public.play_events_raw where event_id=%s',(event.song_time_sec,event.event_id)).fetchone()
        if not saved or str(saved['user_id'])!=str(uid) or str(saved['session_id'])!=str(cycle.session_id) or saved['event_type']!=event.event_type or saved['client_ts']!=event.client_ts or not saved['song_time_matches'] or saved['metadata']!=event.metadata:
            raise HTTPException(409,'Cycle event identity conflict')
    context=c.execute('select started_at from public.student_attempts where attempt_id=%s and user_id=%s',(row['id'],uid)).fetchone()
    if context and context['started_at']!=timestamps[0]:
        raise HTTPException(409,'Finalized cycle start differs from attempt context')
    if context:
        count=c.execute('select count(*) as n from public.student_attempt_events where attempt_id=%s',(row['id'],)).fetchone()['n']
        if int(count)!=len(cycle.events): raise HTTPException(409,'Finalized cycle omits recorded activity')
        for index,event in enumerate(cycle.events):
            saved=c.execute('select *, song_time_sec is not distinct from %s::double precision as song_time_matches from public.student_attempt_events where event_id=%s and attempt_id=%s',(event.song_time_sec,event.event_id,row['id'])).fetchone()
            if not saved or saved['event_sequence']!=index or saved['event_type']!=event.event_type or saved['occurred_at']!=timestamps[index] or not saved['song_time_matches'] or saved['metadata']!=event.metadata:
                raise HTTPException(409,'Finalized cycle differs from recorded activity')
    terminal=cycle.events[-1]
    terminal_metadata=terminal.metadata
    time_playing=terminal_metadata.get('time_playing_sec',terminal.song_time_sec)
    if not finite_number(time_playing) or not 0<=time_playing<=14400:
        raise HTTPException(422,'Invalid terminal time_playing_sec')
    success=terminal_metadata.get('success') is True
    exit_status='abandoned' if terminal.event_type=='exited' else ('succeeded' if success else 'failed')
    completed=terminal.event_type=='finished'
    c.execute('''insert into public.user_play_logs(session_id,user_id,song_id,exercise_id,play_mode,days_since_signup,time_playing,song_time_sec,challenge_recording_id,is_played_in_full,exit_status,started_at,ended_at,events_count)
        values(%s,%s,%s,%s,'challenge',(extract(epoch from (%s::timestamptz-public.piano_registration_time(%s)))/86400.0),%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(session_id) do update set started_at=excluded.started_at,days_since_signup=excluded.days_since_signup,time_playing=excluded.time_playing,song_time_sec=excluded.song_time_sec,challenge_recording_id=excluded.challenge_recording_id,is_played_in_full=excluded.is_played_in_full,exit_status=excluded.exit_status,ended_at=excluded.ended_at,events_count=excluded.events_count,updated_at=now()''',
        (cycle.session_id,uid,row['song_id'],f"challenge:{row['song_id']}",timestamps[0],uid,float(time_playing),terminal.song_time_sec,row['id'],completed,exit_status,timestamps[0],timestamps[-1],len(cycle.events)))

