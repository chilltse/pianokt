"""Explicit research runs; never mutate online pointers, scores, outbox or KT state."""
from tempfile import TemporaryDirectory
from .contracts import AlignmentConfig
from .alignment.feature_extraction import resolve_kc_version
from .online import connect
from .provenance import export_environment
from .run_registry import assert_not_excluded, register_run
from .upload_validation import validate_files
from .worker import build_silver


def reprocess(objects, attempt_ids, environment, *, alignment_version='1.0.0',
              reference_grouping_version='1.0.0', kc_labeler_version=None, dry_run=False):
    environment = export_environment(environment)
    config = AlignmentConfig(version=alignment_version, reference_grouping_version=reference_grouping_version)
    version = resolve_kc_version(kc_labeler_version)
    report = dict(dry_run=dry_run, succeeded=[], failed=[])
    for attempt_id in dict.fromkeys(map(str, attempt_ids)):
        try:
            with connect() as c:
                row = c.execute('select * from public.piano_attempts where id=%s and source_environment=%s', (attempt_id, environment)).fetchone()
                if not row:
                    raise ValueError('Attempt missing or environment mismatch')
                if row['status'] not in ('READY', 'FAILED', 'INVALID'):
                    raise ValueError('Attempt must be terminal before research reprocessing')
                assert_not_excluded(c, row)
            if dry_run:
                report['succeeded'].append(dict(attempt_id=attempt_id, action='would_validate_and_reprocess'))
                continue
            with TemporaryDirectory(prefix='pianokt-reprocess-') as directory:
                files, telemetry, _ = validate_files(objects, row, directory)
                key, result = build_silver(objects, row, files, telemetry, config, kc_labeler_version=version)
            with connect() as c:
                current = c.execute('select * from public.piano_attempts where id=%s for update', (attempt_id,)).fetchone()
                if not current:
                    raise ValueError('Attempt deleted during processing')
                register_run(c, current, key, result, 'research')
            report['succeeded'].append(dict(attempt_id=attempt_id, alignment_run_id=result['alignment_run_id']))
        except Exception as exc:
            report['failed'].append(dict(attempt_id=attempt_id, error_type=type(exc).__name__, reason=str(exc)))
    report['processed_count'] = len(report['succeeded']) + len(report['failed'])
    return report
