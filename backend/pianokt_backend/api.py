"""Supabase-authenticated API. Browser uploads new MIDI directly to private GCS."""
import os
import json
import uuid
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

app=FastAPI(title='PianoKT API',version='0.1.0')
app.add_middleware(CORSMiddleware,allow_origins=os.getenv('FRONTEND_ORIGINS','http://localhost:5173').split(','),allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])

def user(authorization:str=Header(...)):
    if not authorization.startswith('Bearer '): raise HTTPException(401,'Bearer token required')
    try:
        r=httpx.get(os.environ['SUPABASE_URL'].rstrip('/')+'/auth/v1/user',headers={'Authorization':authorization,'apikey':os.environ['SUPABASE_ANON_KEY']},timeout=10)
        if r.status_code!=200: raise HTTPException(401,'Invalid session')
        allowed=httpx.post(os.environ['SUPABASE_URL'].rstrip('/')+'/rest/v1/rpc/check_user_allowed',headers={'Authorization':authorization,'apikey':os.environ['SUPABASE_ANON_KEY']},json={},timeout=10)
        if allowed.status_code!=200: raise HTTPException(503,'Access policy unavailable')
        if allowed.json() is not True: raise HTTPException(403,'Account is not on the access list')
        return str(uuid.UUID(r.json()['id']))
    except httpx.HTTPError as exc: raise HTTPException(503,'Auth unavailable') from exc

def objects(): return Objects(os.environ['RAW_ROOT'])

def signed(key,method):
    store=objects()
    if store.bucket is None: raise HTTPException(503,'Signed uploads require GCS')
    kwargs=dict(version='v4',expiration=timedelta(minutes=10),method=method)
    if method=='PUT': kwargs.update(content_type='audio/midi',headers={'x-goog-if-generation-match':'0'})
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

@app.post('/attempts')
def create_attempt(body:Upload,uid=Depends(user)):
    if len(json.dumps(body.settings))>16000: raise HTTPException(422,'Settings too large')
    for name,maximum in [('client_accuracy_pct',100),('client_difficulty',10000),('played_until_sec',14400)]:
        value=body.settings.get(name)
        if value is not None and (not isinstance(value,(int,float)) or not 0<=value<=maximum):
            raise HTTPException(422,f'Invalid {name}')
    with connect() as c:
        c.execute('insert into public.piano_learner_keys(user_id) values(%s) on conflict do nothing',(uid,))
        learner=c.execute('select learner_key from public.piano_learner_keys where user_id=%s',(uid,)).fetchone()['learner_key']
        prefix=f'midi/{learner}/{body.attempt_id}'
        c.execute('''insert into public.piano_attempts(id,user_id,learner_key,song_id,song_source,song_title,metadata,performance_key,reference_key,performance_hash,reference_hash)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(id) do nothing''',(body.attempt_id,uid,learner,body.song_id,body.song_source,body.song_title,Jsonb(body.model_dump(mode='json')),prefix+'/performance.mid',prefix+'/reference.mid',body.performance_hash,body.reference_hash))
        row=owned_attempt(c,body.attempt_id,uid)
        if not row or row['metadata']!=body.model_dump(mode='json'): raise HTTPException(409,'Attempt identity conflict')
    return dict(attempt_id=str(body.attempt_id),performance_url=signed(row['performance_key'],'PUT'),reference_url=signed(row['reference_key'],'PUT'))

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
