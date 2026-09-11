import json
from ..contracts import stable_id
from .tables import ENVELOPE_SCHEMA, NOTE_SCHEMA, HAND_SCHEMA, SUMMARY_SCHEMA

def publish_alignment(result,tables,source_key):
    run=result['alignment_run_id']
    meta={k:result[k] for k in ['attempt_id','learner_key','song_id']}
    envelope=dict(event_id=run,event_type='alignment.completed',source_key=source_key,payload_json=json.dumps(result,allow_nan=False))
    tables.merge('bronze/alignment_runs',[envelope],ENVELOPE_SCHEMA,'event_id')
    for side,table in [('ref','score_notes'),('performance','performance_notes')]:
        unique={}
        for raw in result['raw_matches']:
            nid=raw.get(side+'_id')
            if nid is None: continue
            key=stable_id(result['score_version'] if side=='ref' else result['attempt_id'],nid)
            unique[key]=dict(event_id=key,event_type=table,source_key=source_key,payload_json=json.dumps({k:v for k,v in raw.items() if k.startswith(side+'_')}))
        tables.merge('bronze/'+table,list(unique.values()),ENVELOPE_SCHEMA,'event_id')
    errors=[]
    notes=[]
    for r in result['notes']:
        label=r['alignment_type']
        if label not in ('match','deletion','insertion') or r['reject_reason'] not in range(7): errors.append('invalid_label')
        if label in ('match','deletion') and r.get('ref_id') is None: errors.append('missing_reference')
        if label in ('match','insertion') and r.get('performance_id') is None: errors.append('missing_performance')
        notes.append(dict(r,**meta,payload_json=json.dumps(r,allow_nan=False)))
    hand_rows=[]
    for hand,a in result['events_data']['data'].items():
        if len({len(v) for v in a.values()})!=1: errors.append('hand_array_length'); continue
        for i in range(len(a['reject_reason'])):
            hand_rows.append(dict(meta,event_id=stable_id(run,hand,i),alignment_run_id=run,hand=hand,**{k:v[i] for k,v in a.items()}))
    if errors:
        tables.merge('quality/alignment_runs',[dict(envelope,payload_json=json.dumps({'errors':errors}))],ENVELOPE_SCHEMA,'event_id')
        raise ValueError('Alignment quality gate failed')
    tables.merge('silver/midi_alignment_events',notes,NOTE_SCHEMA,'alignment_event_id')
    tables.merge('gold/fact_midi_alignment',notes,NOTE_SCHEMA,'alignment_event_id')
    tables.merge('gold/hand_events',hand_rows,HAND_SCHEMA,'event_id')
    summary=dict(meta,alignment_run_id=run,score_version=result['score_version'],alignment_version=result['config']['version'],**result['summary'])
    tables.merge('gold/fact_practice_attempt',[summary],SUMMARY_SCHEMA,'alignment_run_id')
    # Commit manifest last; a Delta transaction is per table, not across this function.
    tables.merge('ops/completed_runs',[envelope],ENVELOPE_SCHEMA,'event_id')

def catch_up(objects,tables,replay=False):
    completed=set() if replay else {r['event_id'] for r in tables.rows('ops/completed_runs')}
    failures=[]; count=0
    for key in objects.keys('alignment'):
        if not key.endswith('/result.json'): continue
        try:
            result=json.loads(objects.read(key))
            if result['alignment_run_id'] in completed: continue
            publish_alignment(result,tables,key); count+=1
        except Exception as exc:
            failures.append(key)
            tables.merge('ops/failures',[dict(event_id=stable_id(key),event_type='materialization.failed',source_key=key,payload_json=json.dumps({'error_type':type(exc).__name__}))],ENVELOPE_SCHEMA,'event_id')
    if failures: raise RuntimeError(f'{len(failures)} failed runs; see ops/failures')
    return count
