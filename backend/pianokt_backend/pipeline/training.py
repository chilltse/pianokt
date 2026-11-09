import json
from .selection import utc, eligible_results, rejection, select_one, attempt_order_key
from ..contracts import stable_id
from ..song_identity import assert_export_identity
from .attempts import filter_context, validate_events
from ..provenance import export_environment
from .kc_versions import result_kc_version, dataset_kc_version

def training_attempt_context(result, context=None):
    """Attempt-level metadata, retained once per run rather than per onset."""
    visit = (context or {}).get('visit_id')
    recorded_visit = result.get('visit_id')
    if visit is not None and recorded_visit is not None and str(visit) != str(recorded_visit):
        raise ValueError('Visit context mismatch')
    settings = result.get('_attempt_settings') or (result.get('practice_metadata') or {}).get('settings') or {}
    keyboard = result.get('midi_keyboard_used')
    original_keyboard = settings.get('midi_keyboard_used')
    if isinstance(keyboard, bool) and isinstance(original_keyboard, bool) and keyboard != original_keyboard:
        raise ValueError('MIDI keyboard context mismatch')
    if not isinstance(keyboard, bool):
        keyboard = original_keyboard if isinstance(original_keyboard, bool) else None
    count = len(result['events_data']['onset_seconds'])
    declared_count = result.get('reference_events_count')
    if declared_count is not None and (type(declared_count) is not int or declared_count != count):
        raise ValueError('Reference event count mismatch')
    visit = visit if visit is not None else recorded_visit
    return dict(midi_keyboard_used=keyboard, visit_id=str(visit) if visit is not None else None,
                reference_events_count=count)


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
            assert_export_identity(r,a)
        started=(a or {}).get('started_at') or r.get('attempt_started_at') or r.get('attempt_created_at')
        if not started or utc(started)>=boundary: continue
        select_one(selected,r)
        data=r.get('events_data',{})
        validate_events(data)
        for i, onset in enumerate(data['onset_seconds']):
            reason = data['reject_reason']['event'][i]
            examples.append(dict(
                learner_key=r['learner_key'], attempt_id=r['attempt_id'],
                alignment_run_id=r['alignment_run_id'], song_id=r.get('song_id'),
                segment_id=r.get('segment_id'), segment_index=r.get('segment_index'),
                midi_sha256=r.get('midi_sha256'), event_index=i, onset_seconds=onset,
                pitches=data['pitches'][i], intervals_seconds=data['intervals_seconds'][i],
                kc_vector=data['kc_vectors'][i], correct=1-int(reason),
                reject_reason={'event': reason, 'note': data['reject_reason']['note'][i]},
                timing_offset_ms=data['timing_offset_ms'][i],
                pitch_offset_semitones=data['pitch_offset_semitones'][i],
                extra_notes=data['extra_notes'][i],
                ordering_timestamp=utc(started).isoformat(),
                ordering_source='started_at' if a or r.get('attempt_started_at') else 'legacy_upload_time'))

    examples.sort(key=lambda e: (attempt_order_key(by_id[e['attempt_id']]) if e['attempt_id'] in by_id else (str(e['learner_key']),utc(e['ordering_timestamp']),'',0,e['attempt_id']))+(e['onset_seconds'],e['event_index']))
    visit_starts = {str(v['id']): v.get('started_at') for v in context['visits']} if context else {}
    provenance=[dict(attempt_id=r['attempt_id'],alignment_run_id=r['alignment_run_id'],
                     visit_started_at=(utc(visit_starts[str(by_id[r['attempt_id']]['visit_id'])]).isoformat() if r['attempt_id'] in by_id and visit_starts.get(str(by_id[r['attempt_id']]['visit_id'])) else None),
                     kc_feature_names=r['events_data'].get('kc_feature_names'),song_id=r.get('song_id'),segment_id=r.get('segment_id'),segment_index=r.get('segment_index'),midi_sha256=r.get('midi_sha256'),matcher_version=r.get('matcher_version'),**training_attempt_context(r,by_id.get(r['attempt_id'])),kc_labeler_version=result_kc_version(r),result_digest=r.get('_committed_digest'))
                for r in sorted(selected.values(),key=lambda r:r['attempt_id'])]
    payload=dict(source_environment=environment,source='postgres-ready-v1' if hasattr(tables,'results') else 'lake-completed-v1',
                 kc_labeler_version=dataset_kc_version(selected.values()),quality_policy='quality-valid-and-not-waiting',runs=provenance,dataset_version=stable_id(cutoff,environment,examples,provenance,dataset_kc_version(selected.values())),cutoff=utc(cutoff).isoformat(),examples=examples,warning='Split students/time before windowing. No trained model is included.')
    objects.json(output_key,payload)
    return payload['dataset_version']
