import json
import os
import uuid
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from psycopg.types.json import Jsonb
from .online import connect
from .storage import Objects
from .contracts import digest,stable_id,AlignmentConfig
from .alignment.service import align

def process(attempt_id):
    attempt_id=str(uuid.UUID(str(attempt_id)))
    with connect() as c:
        row=c.execute("""update public.piano_attempts set status='PROCESSING',lease_until=now()+interval '10 minutes',updated_at=now()
            where id=%s and (status in ('UPLOADED','FAILED') or (status='PROCESSING' and lease_until<now())) returning *""",(attempt_id,)).fetchone()
        if not row:
            current=c.execute('select status from public.piano_attempts where id=%s',(attempt_id,)).fetchone()
            if current and current['status']=='READY': return
            raise RuntimeError('Attempt busy or not finalized')
    store=Objects(os.environ['RAW_ROOT'])
    try:
        marker_key=f'attempt-results/{attempt_id}/manifest.json'
        try:
            marker=json.loads(store.read(marker_key)); result_key=marker['result_key']
            result=json.loads(store.read(result_key))
        except (FileNotFoundError,): result=None
        except Exception as exc:
            from google.api_core.exceptions import NotFound
            if isinstance(exc,NotFound): result=None
            else: raise
        if result is None:
            # Recover a crash between writing the immutable result and its manifest.
            from importlib.metadata import version
            run=stable_id(attempt_id,row['reference_hash'],row['performance_hash'],AlignmentConfig().identity,version('parangonar'))
            result_key=f'alignment/{run}/result.json'
            try:
                result=json.loads(store.read(result_key))
                store.json(marker_key,{'result_key':result_key})
            except FileNotFoundError: result=None
            except Exception as exc:
                from google.api_core.exceptions import NotFound
                if isinstance(exc,NotFound): result=None
                else: raise
        if result is None:
            with TemporaryDirectory(prefix='pianokt-worker-') as tmp:
                files={}
                for kind in ('performance','reference'):
                    data=store.read(row[kind+'_key'])
                    if digest(data)!=row[kind+'_hash']: raise ValueError('MIDI integrity mismatch')
                    files[kind]=Path(tmp)/(kind+'.mid'); files[kind].write_bytes(data)
                result=align(files['reference'],files['performance'],attempt_id,stage_sink=lambda run,data:store.json(f'matching/{run}/matches.json',data))
            result.update(learner_key=str(row['learner_key']),song_id=row['song_id'],practice_metadata=row['metadata'],attempt_created_at=row['created_at'].isoformat(),alignment_completed_at=datetime.now(timezone.utc).isoformat())
            settings=row['metadata'].get('settings',{}); end=settings.get('played_until_sec')
            start=(settings.get('range') or {}).get('start',0)
            for note in result['notes']:
                note['within_observed_range']=end is None or note.get('ref_onset_sec') is None or note['ref_onset_sec']<=end-start
                note['timing_labels_valid']=not settings.get('waiting',False)
            observed=[n for n in result['notes'] if n['within_observed_range']]
            expected=sum(n['alignment_type'] in ('match','deletion') for n in observed)
            correct=sum(n['is_correct'] for n in observed)
            result['summary'].update(expected_notes=expected,correct_notes=correct,accuracy=correct/expected if expected else None,missing_notes=sum(n['alignment_type']=='deletion' for n in observed),timing_labels_valid=not settings.get('waiting',False))
            result_key=f"alignment/{result['alignment_run_id']}/result.json"
            store.json(result_key,result); store.json(marker_key,{'result_key':result_key})
        with connect() as c:
            c.execute('select pg_advisory_xact_lock(hashtext(%s))',(attempt_id,))
            c.execute("update public.piano_attempts set status='READY',result_key=%s,summary=%s,alignment_run_id=%s,error_code=null,lease_until=null,updated_at=now() where id=%s",(result_key,Jsonb(result['summary']),result['alignment_run_id'],attempt_id))
            payload=dict(event_type='alignment.completed',attempt_id=attempt_id,alignment_run_id=result['alignment_run_id'],result_key=result_key)
            c.execute('insert into public.piano_outbox(event_type,aggregate_id,payload) values(%s,%s,%s) on conflict do nothing',('alignment.completed',result['alignment_run_id'],Jsonb(payload)))
    except Exception as exc:
        with connect() as c: c.execute("update public.piano_attempts set status='FAILED',error_code=%s,lease_until=null,updated_at=now() where id=%s and status<>'READY'",(type(exc).__name__,attempt_id))
        raise

def relay(limit=100):
    from google.cloud import pubsub_v1
    publisher=pubsub_v1.PublisherClient(); count=0
    for _ in range(limit):
        with connect() as c:
            row=c.execute('select * from public.piano_outbox where published_at is null order by created_at for update skip locked limit 1').fetchone()
            if not row: break
            data=dict(row['payload'],event_id=str(row['event_id']),event_type=row['event_type'])
            publisher.publish(os.environ['EVENT_TOPIC'],json.dumps(data).encode(),event_type=row['event_type']).result(timeout=30)
            c.execute('update public.piano_outbox set published_at=now() where event_id=%s',(row['event_id'],)); count+=1
    return count
