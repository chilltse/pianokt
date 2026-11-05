"""Upload contracts and transactional cycle finalization shared by API and replay tests."""
import json
import math
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal
from .activity import Activity
import os

class Upload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version:Literal[2]
    attempt_id:uuid.UUID
    song_id:str=Field(min_length=1,max_length=300)
    song_source:str=Field(max_length=50)
    song_title:str|None=Field(default=None,max_length=300)
    performance_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    reference_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    duration_sec:float=Field(gt=0,le=14400)
    settings:dict=Field(default_factory=dict)
    performance_events_hash:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    performance_events_count:int|None=Field(default=None,ge=0,le=50000)

class Finalize(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version:Literal[2]
    activity:Activity

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

def validate_finalization(row, body, uid):
    """One event contract for live activity and immutable finalization."""
    activity = body.activity
    context = activity.attempt
    if str(activity.owner_id) != str(uid):
        raise HTTPException(403, 'Activity belongs to another signed-in user')
    if context is None or str(context.attempt_id) != str(row['id']) or context.song_id != row['song_id'] or context.song_source != row['song_source']:
        raise HTTPException(422, 'Finalization does not match attempt context')
    events = activity.events
    if len(events) < 2 or events[0].event_type != 'play_started' or events[-1].event_type not in ('finished', 'exited'):
        raise HTTPException(422, 'Cycle must start with play_started and end with finished or exited')
    if events[0].occurred_at != context.started_at:
        raise HTTPException(422, 'Start event must match attempt started_at')
    if len({e.event_id for e in events}) != len(events):
        raise HTTPException(422, 'Cycle event IDs must be unique')
    state = 'active'
    for index, event in enumerate(events):
        if event.event_sequence != index:
            raise HTTPException(422, 'Cycle event sequence must be contiguous from zero')
        if len(json.dumps(event.metadata)) > 16000:
            raise HTTPException(422, 'Event metadata too large')
        if 0 < index < len(events)-1:
            if event.event_type == 'paused':
                if state != 'active': raise HTTPException(422, 'Pause requires active playback')
                state = 'paused'
            elif event.event_type == 'resumed':
                if state != 'paused': raise HTTPException(422, 'Resume requires paused playback')
                state = 'active'
            else:
                raise HTTPException(422, 'Cycle contains an invalid intermediate event')
    for previous, current in zip(events, events[1:]):
        if previous.song_time_sec > current.song_time_sec:
            raise HTTPException(422, 'Cycle song position is not monotonic')
        if previous.elapsed_playing_sec > current.elapsed_playing_sec:
            raise HTTPException(422, 'Cycle elapsed playing time is not monotonic')
        if previous.occurred_at > current.occurred_at:
            raise HTTPException(422, 'Cycle events are not chronological')
    return events
