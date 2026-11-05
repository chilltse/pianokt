import json
from .selection import utc, eligible_results, rejection, select_one, attempt_order_key
from ..contracts import stable_id
from .attempts import filter_context
from ..provenance import export_environment

def export_dataset(objects,tables,cutoff,output_key, context=None, run_selection=None, environment='production'):
    environment=export_environment(environment)
    if context is not None: context=filter_context(context,environment)
    boundary=utc(cutoff)
    examples=[]; selected={}
    by_id={str(a['attempt_id']):a for a in context['attempts']} if context is not None else {}
    for r in eligible_results(objects,tables,cutoff,run_selection,environment):
        if rejection(r): continue
        a=by_id.get(r['attempt_id'])
        if context is not None:
            if a is None or utc(a['started_at'])>=boundary or utc(a.get('received_at',a['started_at']))>=boundary: continue
            if not a.get('ended_at') or utc(a['ended_at'])>=boundary: continue
            if str(a['learner_key'])!=str(r['learner_key']): raise ValueError('Learner context mismatch')
        started=(a or {}).get('started_at') or r.get('attempt_started_at') or r.get('attempt_created_at')
        if not started or utc(started)>=boundary: continue
        select_one(selected,r)
        data=r.get('events_data',{})
        onsets=data.get('onset_seconds',[])
        labels=(data.get('reject_reason') or {}).get('event',[])
        if not isinstance(onsets,list) or not isinstance(labels,list) or len(onsets)!=len(labels):
            raise ValueError('Invalid v1.1 attempt events')
        for i,(onset,reason) in enumerate(zip(onsets,labels)):
            examples.append(dict(learner_key=r['learner_key'],attempt_id=r['attempt_id'],alignment_run_id=r['alignment_run_id'],event_index=i,onset_seconds=onset,pitches=data['pitches'][i],kc_vector=data['kc_vectors'][i],correct=1-int(reason),reject_reason=reason,ordering_timestamp=utc(started).isoformat(),ordering_source='started_at' if a or r.get('attempt_started_at') else 'legacy_upload_time'))
    examples.sort(key=lambda e: (attempt_order_key(by_id[e['attempt_id']]) if e['attempt_id'] in by_id else (str(e['learner_key']),utc(e['ordering_timestamp']),'',0,e['attempt_id']))+(e['onset_seconds'],e['event_index']))
    provenance=[dict(attempt_id=r['attempt_id'],alignment_run_id=r['alignment_run_id'],
                     matcher_version=r.get('matcher_version'),result_digest=r.get('_committed_digest'))
                for r in sorted(selected.values(),key=lambda r:r['attempt_id'])]
    payload=dict(source_environment=environment,source='postgres-ready-v1' if hasattr(tables,'results') else 'lake-completed-v1',
                 feature_version='v1.1',quality_policy='quality-valid-and-not-waiting',runs=provenance,dataset_version=stable_id(cutoff,environment,examples,provenance),cutoff=utc(cutoff).isoformat(),examples=examples,warning='Split students/time before windowing. No trained model is included.')
    objects.json(output_key,payload)
    return payload['dataset_version']
