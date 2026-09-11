import json
from datetime import datetime
from ..contracts import stable_id

def export_dataset(objects,tables,cutoff,output_key):
    boundary=datetime.fromisoformat(cutoff.replace('Z','+00:00'))
    if boundary.tzinfo is None: raise ValueError('Cutoff needs timezone')
    examples=[]
    completed={row['event_id'] for row in tables.rows('ops/completed_runs')}
    for key in objects.keys('alignment'):
        if not key.endswith('/result.json'): continue
        r=json.loads(objects.read(key))
        if r['alignment_run_id'] not in completed: continue
        if not r.get('alignment_completed_at') or datetime.fromisoformat(r['alignment_completed_at'])>=boundary: continue
        if not r.get('attempt_created_at') or datetime.fromisoformat(r['attempt_created_at'])>=boundary: continue
        settings=r.get('practice_metadata',{}).get('settings',{})
        if settings.get('waiting'): continue
        end=settings.get('played_until_sec',float('inf'))-(settings.get('range') or {}).get('start',0)
        for hand,a in r['events_data']['data'].items():
            for i,reason in enumerate(a['reject_reason']):
                if reason==-1 or a['onset_time'][i]>end*1000: continue
                examples.append(dict(learner_key=r['learner_key'],attempt_id=r['attempt_id'],alignment_run_id=r['alignment_run_id'],hand=hand,onset_ms=a['onset_time'][i],pitches=a['pitches'][i],correct=int(reason==0),reject_reason=reason,attempt_created_at=r['attempt_created_at']))
    examples.sort(key=lambda e:(e['learner_key'],e['attempt_created_at'],e['attempt_id'],e['onset_ms'],e['hand']))
    versions={}
    for e in examples: versions.setdefault(e['attempt_id'],set()).add(e['alignment_run_id'])
    if any(len(v)>1 for v in versions.values()): raise ValueError('Select one alignment version per attempt before training')
    payload=dict(dataset_version=stable_id(cutoff,examples),cutoff=cutoff,examples=examples,warning='Split students/time before windowing. No trained model is included.')
    objects.json(output_key,payload)
    return payload['dataset_version']
