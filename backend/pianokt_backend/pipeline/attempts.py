"""Attempt-shaped datasets. Ordering/context live in separate snapshot tables."""
import json
from collections import defaultdict
from datetime import datetime, timezone
from ..contracts import stable_id


def utc(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Ordering timestamps must have a timezone')
    return value.astimezone(timezone.utc)


def read_context(c, cutoff):
    """Run in one repeatable-read transaction before reading mutable history."""
    visits = c.execute('''select v.*,k.learner_key from public.student_visits v
        join public.piano_learner_keys k on k.user_id=v.user_id
        where v.started_at<%s and v.received_at<%s''', (cutoff, cutoff)).fetchall()
    attempts = c.execute('''select a.*,k.learner_key,public.piano_registration_time(a.user_id) as registered_at,
        l.time_playing,l.is_played_in_full,l.exit_status
        from public.student_attempts a join public.piano_learner_keys k on k.user_id=a.user_id
        left join public.piano_attempts p on p.id=a.attempt_id
        left join public.user_play_logs l on l.session_id=(p.metadata->>'session_id')::uuid
        where a.started_at<%s and a.received_at<%s''', (cutoff, cutoff)).fetchall()
    events = c.execute('''select e.* from public.student_attempt_events e
        join public.student_attempts a using(attempt_id)
        where a.started_at<%s and e.received_at<%s and e.occurred_at<%s
        order by e.attempt_id,e.event_sequence''', (cutoff, cutoff, cutoff)).fetchall()
    return {'visits': visits, 'attempts': attempts, 'events': events}


def json_value(value):
    if isinstance(value, datetime): return utc(value).isoformat()
    if isinstance(value, dict): return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [json_value(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)): return value
    return str(value)  # UUID / Decimal from psycopg


def validate_events(data):
    names = ('onset_seconds', 'pitches', 'intervals_seconds', 'kc_vectors', 'timing_offset_ms', 'pitch_offset_semitones', 'extra_notes')
    n = len(data['onset_seconds'])
    if any(not isinstance(data.get(k), list) or len(data[k]) != n for k in names):
        raise ValueError('Event arrays must have equal lengths')
    reasons = data['reject_reason']
    if len(reasons['event']) != n or len(reasons['note']) != n:
        raise ValueError('Label arrays must have equal lengths')
    if any(a >= b for a, b in zip(data['onset_seconds'], data['onset_seconds'][1:])):
        raise ValueError('Target onsets must be strictly increasing')
    for i in range(n):
        if reasons['event'][i] not in (0, 1) or len(data['kc_vectors'][i]) != 6:
            raise ValueError('Invalid event label or KC vector')
        for k, array in [('pitches', data['pitches']), ('note', reasons['note']), ('timing', data['timing_offset_ms']), ('pitch', data['pitch_offset_semitones'])]:
            if len(array[i]) != 2 or any(len(array[i][h]) != len(data['pitches'][i][h]) for h in (0, 1)):
                raise ValueError(f'Invalid hand shape: {k}')


def build_record(result, context, visit_index, attempt_index):
    data = result['events_data']
    validate_events(data)
    reasons = data['reject_reason']
    counts = [sum(map(len, hands)) for hands in data['pitches']]
    days = (utc(context['started_at']) - utc(context['registered_at'])).total_seconds() / 86400
    if days < 0: raise ValueError('Attempt precedes signup; check client clock')
    if context.get('time_playing') is None or context.get('is_played_in_full') is None:
        raise ValueError('Missing finalized practice context')
    settings = result.get('practice_metadata', {}).get('settings', {})
    # Reference snapshots are cropped to the observed range: their hash changes
    # when the same assigned exercise is abandoned earlier. Keep that hash in
    # index_map, not in the exercise identity.
    exercise_id = 'exercise-' + stable_id(context['song_source'], context['song_id'],
        {k: settings.get(k) for k in ('assigned_range', 'left', 'right', 'transpose', 'waiting')})
    return dict(
        user_id=str(context['learner_key']), song_id=context['song_id'], exercise_id=exercise_id,
        days_since_signup=round(days, 6), song_type=context['song_source'],
        visit_index=visit_index, attempt_index=attempt_index, play_mode='challenge',
        chords_evaluated=sum(n >= 2 for n in counts),
        chords_successful=sum(n >= 2 and reason == 0 for n, reason in zip(counts, reasons['event'])),
        notes_evaluated=sum(counts), notes_successful=sum(code == 0 for hands in reasons['note'] for hand in hands for code in hand),
        time_playing=float(context['time_playing']), is_played_in_full='t' if context['is_played_in_full'] else 'f',
        exit_status='quit' if context['exit_status'] == 'abandoned' else context['exit_status'],
        exercise_part_index=settings.get('exercise_part_index', 0),
        kc_labeler_version='v1.1', events_data=data,
    )


def export_attempts(objects, tables, cutoff, output_prefix, context):
    boundary = utc(cutoff)
    prefix = output_prefix.rstrip('/')
    visits = [v for v in context['visits'] if utc(v['started_at']) < boundary and utc(v.get('received_at', v['started_at'])) < boundary]
    attempts = [a for a in context['attempts'] if utc(a['started_at']) < boundary and utc(a.get('received_at', a['started_at'])) < boundary]
    visits.sort(key=lambda v: (str(v['learner_key']), utc(v['started_at']), str(v['id'])))
    attempts.sort(key=lambda a: (str(a['learner_key']), utc(a['started_at']), str(a['visit_id']), a['visit_attempt_sequence'], str(a['attempt_id'])))
    visit_indices = {}; attempt_indices = {}; counters = defaultdict(int)
    for v in visits:
        key = str(v['learner_key']); counters[key] += 1
        visit_indices[str(v['id'])] = counters[key]
    counters.clear()
    for a in attempts:
        key = str(a['learner_key']); counters[key] += 1
        attempt_indices[str(a['attempt_id'])] = counters[key]
    by_id = {str(a['attempt_id']): a for a in attempts}
    completed = {row['event_id'] for row in tables.rows('ops/completed_runs')}
    selected = {}; skipped = defaultdict(int)
    for key in objects.keys('alignment'):
        if not key.endswith('/result.json'): continue
        r = json.loads(objects.read(key))
        if r['alignment_run_id'] not in completed: continue
        if not r.get('alignment_completed_at') or utc(r['alignment_completed_at']) >= boundary: continue
        a = by_id.get(r['attempt_id'])
        if a is None or str(a['visit_id']) not in visit_indices:
            skipped['missing_context'] += 1; continue
        if str(r.get('learner_key')) != str(a['learner_key']): raise ValueError('Learner context mismatch')
        quality = r.get('alignment_quality_valid', r.get('diagnostics', {}).get('alignment_quality_valid', r.get('summary', {}).get('alignment_quality_valid', False)))
        if quality is not True:
            skipped['quality_rejected'] += 1; continue
        if r.get('practice_metadata', {}).get('settings', {}).get('waiting'):
            skipped['waiting'] += 1; continue
        if r['attempt_id'] in selected: raise ValueError('Select one alignment version per attempt before exporting')
        selected[r['attempt_id']] = r
    records = []; mappings = []
    for a in attempts:
        aid = str(a['attempt_id']); r = selected.get(aid)
        if r is None: continue
        if not a.get('ended_at') or utc(a['ended_at']) >= boundary:
            skipped['unfinished_at_cutoff'] += 1; continue
        vi, ai = visit_indices[str(a['visit_id'])], attempt_indices[aid]
        records.append(build_record(r, a, vi, ai))
        mappings.append(dict(user_id=str(a['learner_key']), visit_index=vi, attempt_index=ai,
                             attempt_id=aid, visit_id=str(a['visit_id']), alignment_run_id=r['alignment_run_id'], score_version=r['score_version']))
    # No auth user IDs leave the context snapshot. Timestamps remain separate.
    visit_rows = [{**{k: v for k, v in row.items() if k not in ('user_id', 'learner_key')}, 'user_id': str(row['learner_key']), 'visit_index': visit_indices[str(row['id'])]} for row in visits]
    attempt_rows = [{**{k: v for k, v in row.items() if k not in ('user_id', 'learner_key', 'registered_at')}, 'user_id': str(row['learner_key']), 'attempt_index': attempt_indices[str(row['attempt_id'])]} for row in attempts]
    events = [{k: v for k, v in row.items() if k != 'user_id'} for row in context.get('events', [])
              if str(row['attempt_id']) in by_id and utc(row['occurred_at']) < boundary and utc(row.get('received_at', row['occurred_at'])) < boundary]
    snapshots = json_value(dict(visits=visit_rows, attempts=attempt_rows, events=events, index_map=mappings))
    version = stable_id('attempt-v1.2', utc(cutoff).isoformat(), records, snapshots)
    manifest = dict(dataset_version=version, schema_version='1.2', cutoff=utc(cutoff).isoformat(),
                    count=len(records), skipped=dict(skipped), ordering='Per learner: UTC started_at; visit ID, within-visit attempt sequence, attempt ID break ties. Indices assigned before quality filtering.',
                    context_files=['visits.json', 'attempt_metadata.json', 'attempt_events.json', 'index_map.json'],
                    context_semantics='Metadata is captured at export time; cutoff filters accepted history and labels, not historical visit liveness. Preserve this immutable snapshot for reproduction.')
    objects.put(prefix+'/attempts.jsonl', ''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in records).encode())
    for filename, key in [('visits.json','visits'),('attempt_metadata.json','attempts'),('attempt_events.json','events'),('index_map.json','index_map')]:
        objects.json(prefix+'/'+filename, snapshots[key])
    objects.json(prefix+'/manifest.json', manifest)
    return manifest
