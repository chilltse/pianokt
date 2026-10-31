"""Supabase-authenticated API. Browser uploads new MIDI directly to private GCS."""
import os
import json
import math
import uuid
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from fastapi import FastAPI,Depends,Header,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field
from psycopg.types.json import Jsonb
from .online import connect,owned_attempt
from .storage import Objects
from .contracts import check_midi,digest
from .inference import DemoRecommender
from .alignment.service import align

app=FastAPI(title='PianoKT API',version='0.1.0')
app.add_middleware(CORSMiddleware,allow_origins=os.getenv('FRONTEND_ORIGINS','http://localhost:5173').split(','),allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])

def local_development_access_list_bypass_enabled():
    """Allow an authenticated local developer to bypass only the allow-list.

    This is intentionally unavailable in Cloud Run (``K_SERVICE`` is injected
    there) and requires two explicit local environment variables. Authentication
    against Supabase still happens first, so this is not an anonymous/dev token.
    """
    return (
        os.getenv('PIANOKT_ENV')=='development'
        and os.getenv('PIANOKT_LOCAL_DEV_BYPASS')=='1'
        and not os.getenv('K_SERVICE')
    )

def user(authorization:str=Header(...)):
    if not authorization.startswith('Bearer '): raise HTTPException(401,'Bearer token required')
    try:
        r=httpx.get(os.environ['SUPABASE_URL'].rstrip('/')+'/auth/v1/user',headers={'Authorization':authorization,'apikey':os.environ['SUPABASE_ANON_KEY']},timeout=10)
        if r.status_code!=200: raise HTTPException(401,'Invalid session')
        if not local_development_access_list_bypass_enabled():
            allowed=httpx.post(os.environ['SUPABASE_URL'].rstrip('/')+'/rest/v1/rpc/check_user_allowed',headers={'Authorization':authorization,'apikey':os.environ['SUPABASE_ANON_KEY']},json={},timeout=10)
            if allowed.status_code!=200: raise HTTPException(503,'Access policy unavailable')
            if allowed.json() is not True: raise HTTPException(403,'Account is not on the access list')
        return str(uuid.UUID(r.json()['id']))
    except httpx.HTTPError as exc: raise HTTPException(503,'Auth unavailable') from exc

def objects(): return Objects(os.environ['RAW_ROOT'])

def signed(key,method,content_type='audio/midi'):
    store=objects()
    if store.bucket is None: raise HTTPException(503,'Signed uploads require GCS')
    kwargs=dict(version='v4',expiration=timedelta(minutes=10),method=method)
    if method=='PUT': kwargs.update(content_type=content_type,headers={'x-goog-if-generation-match':'0'})
    if os.getenv('SIGNING_SERVICE_ACCOUNT'):
        import google.auth
        from google.auth.transport.requests import Request
        credentials,_=google.auth.default(); credentials.refresh(Request())
        kwargs.update(service_account_email=os.environ['SIGNING_SERVICE_ACCOUNT'],access_token=credentials.token)
    return store.bucket.blob(store._name(key)).generate_signed_url(**kwargs)

class Upload(BaseModel):
    attempt_id:uuid.UUID
    song_id:str=Field(min_length=1,max_length=300)
    song_source:str=Field(max_length=50)
    song_title:str|None=Field(default=None,max_length=300)
    performance_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    reference_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
    duration_sec:float=Field(gt=0,le=14400)
    session_id:uuid.UUID|None=None
    settings:dict=Field(default_factory=dict)
    performance_events_hash:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')
    performance_events_count:int|None=Field(default=None,ge=0,le=50000)

class CycleEvent(BaseModel):
    event_id:uuid.UUID
    event_type:str=Field(pattern='^(play_started|paused|resumed|finished|exited)$')
    song_time_sec:float=Field(ge=0,le=14400)
    client_ts:datetime
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
    for event in cycle.events:
        c.execute('''insert into public.play_events_raw(event_id,session_id,user_id,song_id,exercise_id,play_mode,event_type,song_time_sec,client_ts,metadata)
            values(%s,%s,%s,%s,%s,'challenge',%s,%s,%s,%s) on conflict(event_id) do nothing''',
            (event.event_id,cycle.session_id,uid,row['song_id'],f"challenge:{row['song_id']}",event.event_type,event.song_time_sec,event.client_ts,Jsonb(event.metadata)))
    terminal=cycle.events[-1]
    terminal_metadata=terminal.metadata
    duration=float(row['metadata']['duration_sec'])
    time_playing=terminal_metadata.get('time_playing_sec',terminal.song_time_sec)
    if not isinstance(time_playing,(int,float)) or not 0<=time_playing<=14400:
        raise HTTPException(422,'Invalid terminal time_playing_sec')
    success=terminal_metadata.get('success') is True
    exit_status='abandoned' if terminal.event_type=='exited' else ('succeeded' if success else 'failed')
    completed=terminal.event_type=='finished' or terminal.song_time_sec>=duration*.98
    c.execute('''insert into public.user_play_logs(session_id,user_id,song_id,exercise_id,play_mode,days_since_signup,time_playing,song_time_sec,challenge_recording_id,is_played_in_full,exit_status,started_at,ended_at,events_count)
        values(%s,%s,%s,%s,'challenge',null,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(session_id) do update set time_playing=excluded.time_playing,song_time_sec=excluded.song_time_sec,challenge_recording_id=excluded.challenge_recording_id,is_played_in_full=excluded.is_played_in_full,exit_status=excluded.exit_status,ended_at=excluded.ended_at,events_count=excluded.events_count,updated_at=now()''',
        (cycle.session_id,uid,row['song_id'],f"challenge:{row['song_id']}",float(time_playing),terminal.song_time_sec,row['id'],completed,exit_status,timestamps[0],timestamps[-1],len(cycle.events)))

@app.get('/health')
def health(): return {'status':'ok'}

@app.post('/debug/local-align')
def local_align(body:LocalAlignment):
    """Run and persist a local-only dataset sample; no GCS or database access."""
    if not local_alignment_lab_enabled(): raise HTTPException(404,'Not found')
    try:
        performance=base64.b64decode(body.performance_midi_base64,validate=True)
        reference=base64.b64decode(body.reference_midi_base64,validate=True)
    except Exception as exc: raise HTTPException(422,'Invalid MIDI base64') from exc
    with TemporaryDirectory(prefix='pianokt-local-align-') as tmp:
        root=Path(tmp); performance_path=root/'performance.mid'; reference_path=root/'reference.mid'
        performance_path.write_bytes(performance); reference_path.write_bytes(reference)
        try:
            check_midi(performance_path); check_midi(reference_path)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        events=body.performance_events
        # A random local attempt ID makes every take an immutable file, even
        # when the student plays the same notes twice. This endpoint is only
        # enabled in explicit local-development mode.
        result=align(reference_path,performance_path,f'local-{uuid.uuid4()}',performance_events=events,transport_segments=events.get('transportSegments') if events else None,observed_end_sec=body.observed_end_sec,reference_origin_sec=body.reference_origin_sec)
        output_dir=Path(os.getenv('PIANOKT_LOCAL_ALIGNMENT_OUTPUT_DIR','data/local-alignment')).expanduser().resolve()
        output_dir.mkdir(parents=True,exist_ok=True)
        output_path=output_dir/f"{result['alignment_run_id']}.json"
        output_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return {**result,'local_output_path':str(output_path)}

@app.post('/attempts')
def create_attempt(body:Upload,uid=Depends(user)):
    if len(json.dumps(body.settings))>16000: raise HTTPException(422,'Settings too large')
    validate_alignment_window_settings(body.settings)
    if (body.performance_events_hash is None)!=(body.performance_events_count is None):
        raise HTTPException(422,'Performance event hash and count must be provided together')
    for name,maximum in [('client_accuracy_pct',100),('client_difficulty',10000),('played_until_sec',14400)]:
        value=body.settings.get(name)
        if value is not None and (not finite_number(value) or not 0<=value<=maximum):
            raise HTTPException(422,f'Invalid {name}')
    with connect() as c:
        c.execute('insert into public.piano_learner_keys(user_id) values(%s) on conflict do nothing',(uid,))
        learner=c.execute('select learner_key from public.piano_learner_keys where user_id=%s',(uid,)).fetchone()['learner_key']
        prefix=f'midi/{learner}/{body.attempt_id}'
        events_key=prefix+'/performance-events.json' if body.performance_events_hash else None
        c.execute('''insert into public.piano_attempts(id,user_id,learner_key,song_id,song_source,song_title,metadata,performance_key,reference_key,performance_events_key,performance_hash,reference_hash,performance_events_hash,performance_events_count)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(id) do nothing''',(body.attempt_id,uid,learner,body.song_id,body.song_source,body.song_title,Jsonb(body.model_dump(mode='json')),prefix+'/performance.mid',prefix+'/reference.mid',events_key,body.performance_hash,body.reference_hash,body.performance_events_hash,body.performance_events_count))
        row=owned_attempt(c,body.attempt_id,uid)
        if not row or row['metadata']!=body.model_dump(mode='json'): raise HTTPException(409,'Attempt identity conflict')
    response=dict(attempt_id=str(body.attempt_id),performance_url=signed(row['performance_key'],'PUT'),reference_url=signed(row['reference_key'],'PUT'))
    if row['performance_events_key']:
        response['performance_events_url']=signed(row['performance_events_key'],'PUT','application/json')
    return response

@app.post('/attempts/{attempt_id}/finalize')
def finalize(attempt_id:uuid.UUID,body:Finalize,uid=Depends(user)):
    with connect() as c: row=owned_attempt(c,attempt_id,uid)
    if not row: raise HTTPException(404,'Attempt not found')
    store=objects()
    for kind in ('performance','reference'):
        blob=store.bucket.blob(store._name(row[kind+'_key']))
        try: blob.reload()
        except Exception as exc: raise HTTPException(409,'Upload not complete') from exc
        if blob.size>8_000_000: raise HTTPException(413,'MIDI exceeds 8 MB')
        data=blob.download_as_bytes(if_generation_match=blob.generation)
        if digest(data)!=row[kind+'_hash']: raise HTTPException(409,'Upload digest mismatch')
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'input.mid'; p.write_bytes(data)
            try: check_midi(p)
            except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    if row['performance_events_key']:
        blob=store.bucket.blob(store._name(row['performance_events_key']))
        try: blob.reload()
        except Exception as exc: raise HTTPException(409,'Performance event upload not complete') from exc
        if blob.size>4_000_000: raise HTTPException(413,'Performance event telemetry exceeds 4 MB')
        data=blob.download_as_bytes(if_generation_match=blob.generation)
        if digest(data)!=row['performance_events_hash']: raise HTTPException(409,'Performance event digest mismatch')
        try: validate_performance_events(json.loads(data),row['performance_events_count'])
        except json.JSONDecodeError as exc: raise HTTPException(422,'Invalid performance event telemetry JSON') from exc
    timestamps=validate_cycle(row,body.cycle)
    with connect() as c:
        locked=c.execute('select * from public.piano_attempts where id=%s and user_id=%s for update',(attempt_id,uid)).fetchone()
        if locked['status']=='CREATED':
            c.execute("update public.piano_attempts set status='UPLOADED',updated_at=now() where id=%s",(attempt_id,))
            payload=dict(event_type='performance.uploaded',attempt_id=str(attempt_id),learner_key=str(row['learner_key']))
            c.execute('insert into public.piano_outbox(event_type,aggregate_id,payload) values(%s,%s,%s) on conflict do nothing',('performance.uploaded',str(attempt_id),Jsonb(payload)))
            meta=row['metadata']; settings=meta.get('settings',{})
            c.execute('''insert into public.challenge_recordings(id,user_id,song_source,song_id,song_title,duration_sec,midi_storage_path,midi_keyboard_used,accuracy_pct,difficulty)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(id) do nothing''',(attempt_id,uid,row['song_source'],row['song_id'],row['song_title'],meta['duration_sec'],f'gcs:{attempt_id}',bool(settings.get('midi_keyboard_used',False)),settings.get('client_accuracy_pct') or 0,settings.get('client_difficulty') or 0))
            commit_cycle(c,row,uid,body.cycle,timestamps)
    return {'id':str(attempt_id)}

@app.get('/attempts/{attempt_id}')
def status(attempt_id:uuid.UUID,uid=Depends(user)):
    with connect() as c: row=owned_attempt(c,attempt_id,uid)
    if not row: raise HTTPException(404,'Not found')
    return {k:row[k] for k in ('id','status','summary','error_code','alignment_run_id')}

@app.get('/attempts/{attempt_id}/download')
def download(attempt_id:uuid.UUID,kind:str='performance',uid=Depends(user)):
    if kind not in ('performance','result'): raise HTTPException(422,'Invalid kind')
    with connect() as c: row=owned_attempt(c,attempt_id,uid)
    if not row: raise HTTPException(404,'Not found')
    key=row['performance_key'] if kind=='performance' else row['result_key']
    if not key: raise HTTPException(409,'Result not ready')
    return {'url':signed(key,'GET')}

@app.post('/recommendations')
def recommendations(uid=Depends(user)):
    model=DemoRecommender()
    with connect() as c:
        history=c.execute("select id,summary,alignment_run_id,result_key from public.piano_attempts where user_id=%s and status='READY' order by created_at desc limit 3",(uid,)).fetchall()[::-1]
        pref=c.execute('select genres from public.piano_preferences where user_id=%s',(uid,)).fetchone()
        catalog=c.execute('select song_id,title,difficulty,genres from public.piano_song_catalog where enabled').fetchall()
    for attempt in history:
        result=json.loads(objects().read(attempt['result_key']))
        for k in ('notes','events_data','practice_metadata'): attempt[k]=result.get(k)
    response=dict(model_version=model.version,is_demo=True,input_alignment_run_ids=[a['alignment_run_id'] for a in history],items=model.recommend(history,pref['genres'] if pref else [],catalog,5))
    with connect() as c:
        rid=c.execute('insert into public.piano_recommendations(user_id,attempt_id,model_version,response) values(%s,%s,%s,%s) returning id',(uid,history[-1]['id'] if history else None,model.version,Jsonb(response))).fetchone()['id']
    return dict(recommendation_id=str(rid),**response)

class Feedback(BaseModel):
    event_id:uuid.UUID
    recommendation_id:uuid.UUID
    song_id:str=Field(max_length=300)
    event_type:str=Field(pattern='^(impression|click)$')

@app.post('/recommendations/feedback')
def feedback(body:Feedback,uid=Depends(user)):
    with connect() as c:
        row=c.execute('select response from public.piano_recommendations where id=%s and user_id=%s',(body.recommendation_id,uid)).fetchone()
        if not row or body.song_id not in {s['song_id'] for s in row['response']['items']}: raise HTTPException(404,'Unknown recommendation')
        inserted=c.execute('insert into public.piano_recommendation_feedback(event_id,recommendation_id,user_id,song_id,event_type) values(%s,%s,%s,%s,%s) on conflict do nothing returning event_id',(body.event_id,body.recommendation_id,uid,body.song_id,body.event_type)).fetchone()
        if inserted:
            c.execute('insert into public.piano_learner_keys(user_id) values(%s) on conflict do nothing',(uid,))
            learner=c.execute('select learner_key from public.piano_learner_keys where user_id=%s',(uid,)).fetchone()['learner_key']
            payload=dict(body.model_dump(mode='json'),learner_key=str(learner))
            payload['event_type']='recommendation.'+body.event_type
            c.execute('insert into public.piano_outbox(event_id,event_type,aggregate_id,payload) values(%s,%s,%s,%s) on conflict do nothing',(body.event_id,payload['event_type'],str(body.event_id),Jsonb(payload)))
    return {'ok':True}
