"""Supabase-authenticated API. Browser uploads new MIDI directly to private GCS."""
import os
import json
import math
import logging
import uuid
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from contextlib import asynccontextmanager
from functools import lru_cache
from threading import Lock
from .timing import stage, trace_id, event_count
from fastapi import FastAPI,Depends,Header,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from psycopg.types.json import Jsonb
from .online import connect,owned_attempt,close_pool,enable_pool
from .storage import Objects
from .contracts import check_midi,digest
from .inference import DemoRecommender
from .outbox_dispatch import dispatch_after_commit, close_publisher
from .upload_service import commit_finalization
from .activity import Activity, save_activity
from .provenance import recording_environment
from .upload_contracts import (Upload, Finalize, FinalizeRequest, LocalAlignment, finite_number, local_alignment_lab_enabled, validate_alignment_window_settings, validate_performance_events, validate_finalization)

@lru_cache(maxsize=1)
def auth_client():
    return httpx.Client(timeout=10, limits=httpx.Limits(max_connections=32, max_keepalive_connections=16))

@asynccontextmanager
async def lifespan(app):
    enable_pool()
    try:
        yield
    finally:
        if auth_client.cache_info().currsize:
            auth_client().close()
        auth_client.cache_clear()
        if objects.cache_info().currsize:
            store = objects()
            if store.bucket is not None:
                store.bucket.client.close()
        objects.cache_clear()
        if signing_credentials.cache_info().currsize:
            signing_credentials()[1].session.close()
        signing_credentials.cache_clear()
        close_publisher()
        close_pool()

app=FastAPI(title='PianoKT API',version='0.1.0',lifespan=lifespan)

@app.middleware('http')
async def request_timing(request, call_next):
    # Path has no query string, so signed credentials cannot enter these logs.
    token = trace_id.set(str(uuid.uuid4()))
    try:
        try:
            with stage('http.' + request.method + '.' + ('activity' if request.url.path == '/activity' else 'finalize' if request.url.path.endswith('/finalize') else 'other')):
                response = await call_next(request)
        except Exception:
            # Return through the outer CORS middleware, rather than letting
            # ServerErrorMiddleware emit an unadorned 500 outside CORS.
            logging.getLogger(__name__).exception('Unhandled request error request_id=%s method=%s path=%s',
                trace_id.get(),request.method,request.url.path)
            response = JSONResponse(status_code=500,content={
                'detail':'Internal server error; check backend logs using request_id',
                'request_id':trace_id.get(),
            })
        response.headers['X-Request-ID'] = trace_id.get()
        return response
    finally:
        trace_id.reset(token)


def frontend_origins(value=None):
    configured = os.getenv('FRONTEND_ORIGINS') if value is None else value
    if not configured:
        configured = 'http://localhost:5173,http://127.0.0.1:5173'
    return list(dict.fromkeys(origin.strip().rstrip('/') for origin in configured.split(',') if origin.strip()))

app.add_middleware(CORSMiddleware,allow_origins=frontend_origins(),allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'],expose_headers=['X-Request-ID'])

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
    missing=[name for name in ('SUPABASE_URL','SUPABASE_ANON_KEY') if not os.getenv(name)]
    if missing:
        raise HTTPException(503,'Backend configuration missing: '+', '.join(missing)+'. Start local API with --env-file backend/.env.local')
    try:
        with stage('auth.user'):
            r=auth_client().get(os.environ['SUPABASE_URL'].rstrip('/')+'/auth/v1/user',headers={'Authorization':authorization,'apikey':os.environ['SUPABASE_ANON_KEY']},timeout=10)
        if r.status_code!=200: raise HTTPException(401,'Invalid session')
        if not local_development_access_list_bypass_enabled():
            with stage('auth.allowlist'):
                allowed=auth_client().post(os.environ['SUPABASE_URL'].rstrip('/')+'/rest/v1/rpc/check_user_allowed',headers={'Authorization':authorization,'apikey':os.environ['SUPABASE_ANON_KEY']},json={},timeout=10)
            if allowed.status_code!=200: raise HTTPException(503,'Access policy unavailable')
            if allowed.json() is not True: raise HTTPException(403,'Account is not on the access list')
        return str(uuid.UUID(r.json()['id']))
    except httpx.HTTPError as exc: raise HTTPException(503,'Auth unavailable') from exc

@lru_cache(maxsize=1)
def objects(): return Objects(os.environ['RAW_ROOT'])

_credentials_lock = Lock()

@lru_cache(maxsize=1)
def signing_credentials():
    import google.auth
    from google.auth.transport.requests import Request
    credentials, _ = google.auth.default()
    return credentials, Request()

def signing_token():
    from google.auth.credentials import TokenState
    with _credentials_lock:
        credentials, request = signing_credentials()
        if credentials.token_state != TokenState.FRESH:
            with stage('gcs.credentials_refresh'):
                credentials.refresh(request)
        return credentials.token

def signed(key,method,content_type='audio/midi'):
    store=objects()
    if store.bucket is None: raise HTTPException(503,'Signed uploads require GCS')
    kwargs=dict(version='v4',expiration=timedelta(minutes=10),method=method)
    if method=='PUT': kwargs.update(content_type=content_type,headers={'x-goog-if-generation-match':'0'})
    if os.getenv('SIGNING_SERVICE_ACCOUNT'):
        kwargs.update(service_account_email=os.environ['SIGNING_SERVICE_ACCOUNT'],access_token=signing_token())
    with stage('gcs.sign'):
        return store.bucket.blob(store._name(key)).generate_signed_url(**kwargs)

@app.post('/activity')
def activity(body:Activity,uid=Depends(user)):
    with connect() as c:
        save_activity(c,uid,body)
    return {'ok':True}

@app.get('/health')
def health(): return {'status':'ok'}

@app.post('/debug/local-align')
def local_align(body:LocalAlignment):
    """Run and persist a local-only dataset sample; no GCS or database access."""
    if not local_alignment_lab_enabled(): raise HTTPException(404,'Not found')
    from .alignment.service import align
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
        Objects(str(output_dir)).json(output_path.name,result)
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
    environment = recording_environment()
    metadata = body.model_dump(mode='json')
    with connect() as c:
        from .song_identity import check_catalog_identity
        check_catalog_identity(c,body)
        c.execute('insert into public.piano_learner_keys(user_id) values(%s) on conflict do nothing',(uid,))
        learner=c.execute('select learner_key from public.piano_learner_keys where user_id=%s',(uid,)).fetchone()['learner_key']
        prefix=f'midi/{learner}/{body.attempt_id}'
        events_key=prefix+'/performance-events.json' if body.performance_events_hash else None
        c.execute('''insert into public.piano_attempts(id,user_id,learner_key,song_id,song_source,song_title,metadata,performance_key,reference_key,performance_events_key,performance_hash,reference_hash,performance_events_hash,performance_events_count,source_environment,segment_id,segment_index,midi_sha256)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(id) do nothing''',(body.attempt_id,uid,learner,body.song_id,body.song_source,body.song_title,Jsonb(metadata),prefix+'/performance.mid',prefix+'/reference.mid',events_key,body.performance_hash,body.reference_hash,body.performance_events_hash,body.performance_events_count,environment,body.segment_id,body.segment_index,body.midi_sha256))
        row=owned_attempt(c,body.attempt_id,uid)
        if not row or row['source_environment']!=environment or row['metadata']!=metadata: raise HTTPException(409,'Attempt identity conflict')
    response=dict(attempt_id=str(body.attempt_id),performance_url=signed(row['performance_key'],'PUT'),reference_url=signed(row['reference_key'],'PUT'))
    if row['performance_events_key']:
        response['performance_events_url']=signed(row['performance_events_key'],'PUT','application/json')
    return response

@app.post('/attempts/{attempt_id}/finalize', status_code=202)
def finalize(attempt_id:uuid.UUID,body:FinalizeRequest,uid=Depends(user)):
    with stage('http.finalize'):
        if body.schema_version != 3:
            raise HTTPException(422, 'Async finalize requires schema_version=3; refresh the frontend')
        event_count('finalize.activity_events', len(body.activity.events))
        # No GCS network operations here: DB commit is the durable handoff.
        with connect() as c:
            row=owned_attempt(c,attempt_id,uid)
            if not row: raise HTTPException(404,'Attempt not found')
            row=commit_finalization(c,row,uid,body.resolve(row))
        dispatch_after_commit(row['outbox_event_id'])
        return {'id':str(attempt_id),'status':row['status'],'validated_at':row['validated_at']}


@app.get('/attempts/{attempt_id}')
def status(attempt_id:uuid.UUID,uid=Depends(user)):
    with connect() as c: row=owned_attempt(c,attempt_id,uid)
    if not row: raise HTTPException(404,'Not found')
    return {k:row[k] for k in ('id','status','summary','error_code','alignment_run_id','accepted_at','validated_at','validation_error','next_retry_at')}

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
        history=c.execute("select p.id,p.summary,p.alignment_run_id,p.result_key from public.piano_attempts p left join public.student_attempts a on a.attempt_id=p.id where p.user_id=%s and p.source_environment=%s and p.status='READY' and p.summary->>'alignment_quality_valid'='true' order by coalesce(a.started_at,p.created_at) desc,p.id desc limit 3",(uid,recording_environment())).fetchall()[::-1]
        pref=c.execute('select genres from public.piano_preferences where user_id=%s',(uid,)).fetchone()
        catalog=c.execute("""select song_id::text as song_id,segment_id::text as segment_id,segment_index, title || ' — ' || coalesce(segment_title,'Segment ' || segment_index) as title,difficulty,genres from public.piano_public_song_catalog""").fetchall()
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
    segment_id:uuid.UUID
    event_type:str=Field(pattern='^(impression|click)$')

@app.post('/recommendations/feedback')
def feedback(body:Feedback,uid=Depends(user)):
    with connect() as c:
        row=c.execute('select response from public.piano_recommendations where id=%s and user_id=%s',(body.recommendation_id,uid)).fetchone()
        if not row or not any(s['song_id']==body.song_id and s.get('segment_id')==str(body.segment_id) for s in row['response']['items']): raise HTTPException(404,'Unknown recommendation')
        inserted=c.execute('insert into public.piano_recommendation_feedback(event_id,recommendation_id,user_id,song_id,segment_id,event_type) values(%s,%s,%s,%s,%s,%s) on conflict do nothing returning event_id',(body.event_id,body.recommendation_id,uid,body.song_id,body.segment_id,body.event_type)).fetchone()
        if not inserted:
            existing=c.execute('select * from public.piano_recommendation_feedback where event_id=%s',(body.event_id,)).fetchone()
            if not existing or str(existing['user_id'])!=str(uid) or str(existing['recommendation_id'])!=str(body.recommendation_id) or existing['song_id']!=body.song_id or str(existing['segment_id'])!=str(body.segment_id) or existing['event_type']!=body.event_type:
                raise HTTPException(409,'Feedback event identity conflict')
        if inserted:
            c.execute('insert into public.piano_learner_keys(user_id) values(%s) on conflict do nothing',(uid,))
            learner=c.execute('select learner_key from public.piano_learner_keys where user_id=%s',(uid,)).fetchone()['learner_key']
            payload=dict(body.model_dump(mode='json'),learner_key=str(learner))
            payload['event_type']='recommendation.'+body.event_type
            c.execute('insert into public.piano_outbox(event_id,event_type,aggregate_id,payload) values(%s,%s,%s,%s) on conflict do nothing',(body.event_id,payload['event_type'],str(body.event_id),Jsonb(payload)))
    return {'ok':True}
