"""Thin DB snapshot adapter; no lake imports, object listing, or DB downloads."""
import json
from ..contracts import digest
from ..storage_layout import DATA_ARCHITECTURE_VERSION, silver_result_key
from .selection import utc
from .attempts import validate_events
from ..provenance import export_environment


def read_committed(connection, cutoff, environment='production', *, run_selection=None,
                   alignment_version=None, reference_grouping_version=None, kc_labeler_version=None,
                   learner_key=None, song_id=None, segment_id=None):
    """One repeatable-read snapshot; registry is the sole commit authority."""
    filters = ["r.source_environment=%s", "r.committed_at < %s", "r.data_architecture_version='3'"]
    params = [export_environment(environment), cutoff]
    historical = run_selection is not None or any((alignment_version, reference_grouping_version, kc_labeler_version))
    if not historical:
        filters += ["p.status='READY'", "p.data_architecture_version='3'", "p.alignment_run_id=r.alignment_run_id",
                    "p.result_digest=r.result_digest", "r.processing_purpose='online'"]
    for field, value in dict(alignment_version=alignment_version, reference_grouping_version=reference_grouping_version,
                             kc_labeler_version=kc_labeler_version, learner_key=learner_key, song_id=song_id, segment_id=segment_id).items():
        if value is not None:
            filters.append(f'r.{field}=%s'); params.append(value)
    filters.append("""not exists (select 1 from public.piano_data_exclusions e
        where e.source_environment=r.source_environment and
        ((e.scope='attempt' and e.identity=r.attempt_id::text) or
         (e.scope='learner' and e.identity=r.learner_key::text) or
         (e.scope='song' and e.identity=r.song_id) or
         (e.scope='segment' and e.identity=r.segment_id::text)))""")
    rows = connection.execute("""select r.*,r.committed_at as result_committed_at,p.metadata
        from public.piano_alignment_runs r join public.piano_attempts p on p.id=r.attempt_id
        and p.learner_key=r.learner_key and p.source_environment=r.source_environment
        where """ + ' and '.join(filters) + ' order by r.learner_key,r.attempt_id,r.alignment_run_id', params).fetchall()
    if run_selection is not None:
        rows = [r for r in rows if run_selection.get(str(r['attempt_id'])) == r['alignment_run_id']]
        if set(run_selection) != {str(r['attempt_id']) for r in rows}:
            raise ValueError('Selected runs missing, excluded, or outside selection')
    ids = [str(r['attempt_id']) for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Multiple eligible runs; explicit run-selection required')
    withdrawn = connection.execute("select identity from public.piano_data_exclusions where source_environment=%s and scope='dataset'", (environment,)).fetchall()
    selection = dict(policy='explicit-runs' if run_selection is not None else ('version-filter' if historical else 'current-online'),
                     run_selection=run_selection, alignment_version=alignment_version,
                     reference_grouping_version=reference_grouping_version, kc_labeler_version=kc_labeler_version,
                     learner_key=str(learner_key) if learner_key else None, song_id=song_id, segment_id=str(segment_id) if segment_id else None)
    return CommittedResults(rows, selection=selection, withdrawn={r['identity'] for r in withdrawn})


class CommittedResults:
    def __init__(self, rows, *, selection=None, withdrawn=()):
        self.selection = selection or {"policy": "registry-snapshot"}
        self.withdrawn = frozenset(withdrawn)
        self.rows = tuple(dict(row) for row in rows)

    def results(self, objects, cutoff, run_selection=None, environment='production'):
        boundary = utc(cutoff)
        environment = export_environment(environment)
        rows = [r for r in self.rows if r.get('source_environment') == environment and r.get('data_architecture_version') == DATA_ARCHITECTURE_VERSION]
        if run_selection is not None:
            available = {str(r['attempt_id']): r['alignment_run_id'] for r in rows
                         if r.get('result_committed_at') and utc(r['result_committed_at']) < boundary}
            if any(available.get(aid) != run for aid, run in run_selection.items()):
                raise ValueError('Selected run is not a verifiable registry commit before cutoff')
            rows = [r for r in rows if str(r['attempt_id']) in run_selection]
        for row in rows:
            aid = str(row['attempt_id'])
            if not row.get('result_digest') or not row.get('result_committed_at'):
                raise ValueError(f'Unverified registry commit: {aid}')
            if utc(row['result_committed_at']) >= boundary:
                continue
            key = row['result_key']
            if key != silver_result_key(row['alignment_run_id']):
                raise ValueError(f'Invalid committed result key: {aid}')
            raw = objects.read(key)  # Missing/corrupt objects must abort publication.
            if digest(raw) != row['result_digest']:
                raise ValueError(f'Committed result digest mismatch: {aid}')
            result = json.loads(raw)
            expected = dict(attempt_id=aid, learner_key=str(row['learner_key']),
                            alignment_run_id=row['alignment_run_id'],
                            score_version=row['reference_hash'],
                            performance_hash=row['performance_hash'], schema_version=3, data_architecture_version=DATA_ARCHITECTURE_VERSION, source_environment=environment)
            expected.update({key: str(row[key]) if key == 'segment_id' and row[key] is not None else row[key]
                             for key in ('song_id','segment_id','segment_index','midi_sha256') if key in row})
            if any(result.get(k) != v for k, v in expected.items()):
                raise ValueError(f'Committed result identity/schema mismatch: {aid}')
            for field in ('alignment_version','matcher_version','reference_grouping_version','kc_labeler_version'):
                if field in row and result.get(field) != row[field]:
                    raise ValueError('Committed version mismatch: ' + field)
            if 'telemetry_hash' in row and result.get('performance_events_hash') != row['telemetry_hash']:
                raise ValueError('Committed telemetry hash mismatch')
            if result.get('summary') != row['summary']:
                raise ValueError(f'Committed summary mismatch: {aid}')
            if not result.get('matcher_version') or not result.get('config'):
                raise ValueError(f'Missing alignment version: {aid}')
            if not result.get('alignment_completed_at') or utc(result['alignment_completed_at']) >= boundary:
                raise ValueError(f'Committed result completion outside cutoff: {aid}')
            validate_events(result['events_data'])
            # Snapshot metadata supplies context for older immutable results.
            # Keep it separate from the digest-verified artifact itself.
            result['_attempt_settings'] = (row.get('metadata') or {}).get('settings') or {}
            result['_committed_digest'] = row['result_digest']
            yield result
