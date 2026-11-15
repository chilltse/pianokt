"""Shared, deterministic Gold lineage metadata."""
from ..contracts import stable_id
from ..storage_layout import DATA_ARCHITECTURE_VERSION
from .selection import utc
from datetime import datetime, timezone


def lineage(kind, results, cutoff, environment, run_selection, excluded):
    results = list(results)
    runs = [dict(attempt_id=r['attempt_id'], alignment_run_id=r['alignment_run_id'],
                 learner_key=r['learner_key'], song_id=r.get('song_id'), segment_id=r.get('segment_id'),
                 result_digest=r['_committed_digest']) for r in sorted(results, key=lambda r: r['attempt_id'])]
    metadata = dict(dataset_type=kind, data_architecture_version=DATA_ARCHITECTURE_VERSION,
                    created_at=datetime.now(timezone.utc).isoformat(), cutoff=utc(cutoff).isoformat(), source_environment=environment,
                    source_selection=dict(run_selection=run_selection, policy='explicit-registry-selection' if run_selection else 'registry-snapshot'),
                    quality_policy_version='quality-valid-and-not-waiting-v1',
                    ordering_policy='learner/UTC-start/visit/visit-attempt-sequence/attempt/onset/index',
                    source_runs=runs, source_snapshot_digest=stable_id(runs),
                    student_count=len({r['learner_key'] for r in results}),
                    attempt_count=len(results), excluded_count=sum(excluded.values()), exclusion_reasons=dict(excluded))
    for field in ('alignment_version', 'matcher_version', 'reference_grouping_version', 'kc_labeler_version'):
        metadata[field + 's'] = sorted({r[field] for r in results})
    return metadata
