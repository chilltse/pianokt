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
        diagnostics=r.get('diagnostics',{})
        summary=r.get('summary',{})
        # Pre-quality-gate results are replay artifacts, not automatically
        # trustworthy labels. A historical run can be explicitly re-aligned
        # with the current matcher if it should contribute to training.
        quality=r.get('alignment_quality_valid',diagnostics.get('alignment_quality_valid',summary.get('alignment_quality_valid',False)))
        # A terminal quality rejection is intentionally committed so the raw
        # result can be audited, but it must never become a training example.
        if quality is not True: continue
        if not r.get('alignment_completed_at') or datetime.fromisoformat(r['alignment_completed_at'])>=boundary: continue
        if not r.get('attempt_created_at') or datetime.fromisoformat(r['attempt_created_at'])>=boundary: continue
        settings=r.get('practice_metadata',{}).get('settings',{})
        if settings.get('waiting'): continue
        data=r.get('events_data',{})
        onsets=data.get('onset_seconds',[])
        labels=(data.get('reject_reason') or {}).get('event',[])
        if not isinstance(onsets,list) or not isinstance(labels,list) or len(onsets)!=len(labels):
            raise ValueError('Invalid v1.1 attempt events')
        for i,(onset,reason) in enumerate(zip(onsets,labels)):
            examples.append(dict(learner_key=r['learner_key'],attempt_id=r['attempt_id'],alignment_run_id=r['alignment_run_id'],event_index=i,onset_seconds=onset,pitches=data['pitches'][i],kc_vector=data['kc_vectors'][i],correct=1-int(reason),reject_reason=reason,attempt_created_at=r['attempt_created_at']))
    examples.sort(key=lambda e:(e['learner_key'],e['attempt_created_at'],e['attempt_id'],e['onset_seconds'],e['event_index']))
    versions={}
    for e in examples: versions.setdefault(e['attempt_id'],set()).add(e['alignment_run_id'])
    if any(len(v)>1 for v in versions.values()): raise ValueError('Select one alignment version per attempt before training')
    payload=dict(dataset_version=stable_id(cutoff,examples),cutoff=cutoff,examples=examples,warning='Split students/time before windowing. No trained model is included.')
    objects.json(output_key,payload)
    return payload['dataset_version']
