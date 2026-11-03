"""Atomic business commit. Caller owns the database transaction."""
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from .upload_contracts import commit_cycle

def commit_finalization(c,row,uid,body,timestamps):
    attempt_id=row['id']
    c.execute('select pg_advisory_xact_lock(hashtext(%s))',(str(attempt_id),))
    locked=c.execute('select * from public.piano_attempts where id=%s and user_id=%s for update',(attempt_id,uid)).fetchone()
    if not locked: raise HTTPException(404,'Attempt not found')
    canonical=body.model_dump(mode='json')
    if locked.get('finalization') is not None and locked['finalization']!=canonical:
        raise HTTPException(409,'Finalization identity conflict')
    if locked['status']=='CREATED':
        c.execute("update public.piano_attempts set status='UPLOADED',finalization=%s,updated_at=now() where id=%s",(Jsonb(canonical),attempt_id,))
        payload=dict(event_type='performance.uploaded',attempt_id=str(attempt_id),learner_key=str(row['learner_key']))
        c.execute('insert into public.piano_outbox(event_type,aggregate_id,payload) values(%s,%s,%s) on conflict do nothing',('performance.uploaded',str(attempt_id),Jsonb(payload)))
        meta=row['metadata']; settings=meta.get('settings',{})
        c.execute('''insert into public.challenge_recordings(id,user_id,song_source,song_id,song_title,duration_sec,midi_storage_path,midi_keyboard_used,accuracy_pct,difficulty,client_accuracy_pct,score_source)
            values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')''',(attempt_id,uid,row['song_source'],row['song_id'],row['song_title'],meta['duration_sec'],f'gcs:{attempt_id}',bool(settings.get('midi_keyboard_used',False)),0,settings.get('client_difficulty') or 0,settings.get('client_accuracy_pct')))
        commit_cycle(c,row,uid,body.cycle,timestamps)
