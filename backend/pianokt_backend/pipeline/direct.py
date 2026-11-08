"""Thin DB snapshot adapter; no lake imports, object listing, or DB downloads."""
import json
from ..contracts import digest
from .selection import utc
from .attempts import validate_events
from ..provenance import export_environment


def read_committed(connection, cutoff, environment='production'):
    """Called alongside read_context in one short repeatable-read transaction."""
    rows = connection.execute("""
        select id as attempt_id, learner_key, alignment_run_id, result_key,
               reference_hash, performance_hash, result_digest,song_id,segment_id,segment_index,midi_sha256,
               result_committed_at, summary, source_environment
        from public.piano_attempts
        where status='READY' and source_environment=%s and
              (result_committed_at < %s or result_committed_at is null)
        order by learner_key,id
    """, (export_environment(environment),cutoff)).fetchall()
    return CommittedResults(rows)


class CommittedResults:
    def __init__(self, rows):
        self.rows = tuple(dict(row) for row in rows)

    def results(self, objects, cutoff, run_selection=None, environment='production'):
        boundary = utc(cutoff)
        environment = export_environment(environment)
        rows = [r for r in self.rows if r.get('source_environment') == environment]
        if run_selection is not None:
            available = {str(r['attempt_id']): r['alignment_run_id'] for r in rows
                         if r.get('result_committed_at') and utc(r['result_committed_at']) < boundary}
            if any(available.get(aid) != run for aid, run in run_selection.items()):
                raise ValueError('Selected run is not a verifiable current READY commit before cutoff')
            rows = [r for r in rows if str(r['attempt_id']) in run_selection]
        for row in rows:
            aid = str(row['attempt_id'])
            if not row.get('result_digest') or not row.get('result_committed_at'):
                raise ValueError(f'Unverified legacy READY result: {aid}; use lake baseline or audited migration')
            if utc(row['result_committed_at']) >= boundary:
                continue
            key = row['result_key']
            if key != f"alignment/{row['alignment_run_id']}/result.json":
                raise ValueError(f'Invalid committed result key: {aid}')
            raw = objects.read(key)  # Missing/corrupt objects must abort publication.
            if digest(raw) != row['result_digest']:
                raise ValueError(f'Committed result digest mismatch: {aid}')
            result = json.loads(raw)
            expected = dict(attempt_id=aid, learner_key=str(row['learner_key']),
                            alignment_run_id=row['alignment_run_id'],
                            score_version=row['reference_hash'],
                            performance_hash=row['performance_hash'], schema_version=3, source_environment=environment)
            expected.update({key: str(row[key]) if key == 'segment_id' and row[key] is not None else row[key]
                             for key in ('song_id','segment_id','segment_index','midi_sha256') if key in row})
            if any(result.get(k) != v for k, v in expected.items()):
                raise ValueError(f'Committed result identity/schema mismatch: {aid}')
            if result.get('summary') != row['summary']:
                raise ValueError(f'Committed summary mismatch: {aid}')
            if not result.get('matcher_version') or not result.get('config'):
                raise ValueError(f'Missing alignment version: {aid}')
            if not result.get('alignment_completed_at') or utc(result['alignment_completed_at']) >= boundary:
                raise ValueError(f'Committed result completion outside cutoff: {aid}')
            validate_events(result['events_data'])
            result['_committed_digest'] = row['result_digest']
            yield result
