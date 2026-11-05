"""Shared deterministic eligibility and UTC ordering for dataset serializers."""
import json
from datetime import datetime, timezone


def utc(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Ordering timestamps must have a timezone')
    return value.astimezone(timezone.utc)


def eligible_results(objects, tables, cutoff, run_selection=None, environment='production'):
    """An explicit selection maps attempt ID to immutable run ID; no latest-wins."""
    if tables is None:
        raise ValueError('A committed result source is required')
    if hasattr(tables, 'results'):
        yield from tables.results(objects, cutoff, run_selection, environment)
        return
    boundary = utc(cutoff)
    completed = {str(row['event_id']) for row in tables.rows('ops/completed_runs')}
    seen=set()
    for key in objects.keys('alignment'):
        if not key.endswith('/result.json'): continue
        r = json.loads(objects.read(key))
        if r.get('source_environment') != environment: continue
        if r['alignment_run_id'] not in completed: continue
        if not r.get('alignment_completed_at') or utc(r['alignment_completed_at']) >= boundary: continue
        if run_selection is not None and run_selection.get(r['attempt_id']) != r['alignment_run_id']: continue
        seen.add(r['attempt_id'])
        yield r
    if run_selection is not None and set(run_selection)-seen:
        raise ValueError('Selected runs are missing, uncommitted or outside cutoff: '+str(sorted(set(run_selection)-seen)))


def rejection(result):
    quality = result.get('alignment_quality_valid', result.get('diagnostics', {}).get('alignment_quality_valid', result.get('summary', {}).get('alignment_quality_valid', False)))
    if quality is not True: return 'quality_rejected'
    if result.get('practice_metadata', {}).get('settings', {}).get('waiting'): return 'waiting'
    return None


def select_one(selected, result):
    if result['attempt_id'] in selected:
        raise ValueError('Select one alignment version per attempt before exporting')
    selected[result['attempt_id']] = result


def attempt_order_key(attempt):
    return (str(attempt['learner_key']),utc(attempt['started_at']),str(attempt['visit_id']),attempt['visit_attempt_sequence'],str(attempt['attempt_id']))
