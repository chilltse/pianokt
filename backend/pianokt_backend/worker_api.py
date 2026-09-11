"""Deploy ONLY as private Cloud Run service with IAM-authenticated Pub/Sub push."""
import base64
import json
import subprocess
import sys
import uuid
from fastapi import FastAPI,HTTPException
app=FastAPI(title='PianoKT private worker')

@app.get('/health')
def health(): return {'status':'ok'}

@app.post('/pubsub')
def receive(envelope:dict):
    try:
        data=json.loads(base64.b64decode(envelope['message']['data'],validate=True))
        if data['event_type']=='performance.uploaded':
            attempt=str(uuid.UUID(data['attempt_id']))
            subprocess.run([sys.executable,'-m','pianokt_backend.cli','worker',attempt],check=True,timeout=480)
    except Exception as exc: raise HTTPException(503,'Worker failed; retry or inspect dead letter queue') from exc
    return {'ok':True}
