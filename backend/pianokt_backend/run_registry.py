"""Immutable Silver lineage. Caller owns the fenced transaction."""
import json
from psycopg.types.json import Jsonb
from .contracts import digest, stable_id
from .storage_layout import DATA_ARCHITECTURE_VERSION, silver_result_key


def result_bytes(result):
    return json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def assert_not_excluded(c, row):
    identities = [('attempt', str(row['id'])), ('learner', str(row['learner_key'])),
                  ('song', str(row.get('song_id'))), ('segment', str(row.get('segment_id')))]
    for scope, identity in identities:
        if c.execute('select 1 from public.piano_data_exclusions where source_environment=%s and scope=%s and identity=%s',
                     (row['source_environment'], scope, identity)).fetchone():
            raise ValueError('Data excluded: ' + scope)


def register_run(c, row, result_key, result, purpose):
    assert_not_excluded(c, row)
    if result_key != silver_result_key(result['alignment_run_id']) or result.get('data_architecture_version') != DATA_ARCHITECTURE_VERSION:
        raise ValueError('Not a V3 Silver result')
    expected = dict(attempt_id=str(row['id']), learner_key=str(row['learner_key']),
                    source_environment=row['source_environment'], score_version=row['reference_hash'],
                    performance_hash=row['performance_hash'], performance_events_hash=row.get('performance_events_hash'))
    expected.update({k: str(row[k]) if k == 'segment_id' and row.get(k) is not None else row.get(k) for k in ('song_id','segment_id','segment_index','midi_sha256')})
    if any(result.get(k) != v for k, v in expected.items()):
        raise ValueError('Run input identity conflict')
    fields = dict(alignment_run_id=result['alignment_run_id'], attempt_id=row['id'], learner_key=row['learner_key'],
                  song_id=row['song_id'], segment_id=row.get('segment_id'), segment_index=row.get('segment_index'),
                  midi_sha256=row.get('midi_sha256'), reference_hash=row['reference_hash'], performance_hash=row['performance_hash'],
                  telemetry_hash=row.get('performance_events_hash'), result_key=result_key, result_digest=digest(result_bytes(result)),
                  result_schema_version=result['schema_version'], alignment_version=result['alignment_version'],
                  matcher_version=result['matcher_version'], reference_grouping_version=result['reference_grouping_version'],
                  kc_labeler_version=result['kc_labeler_version'], config_digest=stable_id(result['config']),
                  source_environment=row['source_environment'], quality_valid=result['alignment_quality_valid'],
                  quality_reason=result['alignment_quality_reason'], processing_purpose=purpose, summary=Jsonb(result['summary']))
    c.execute(f"insert into public.piano_alignment_runs ({','.join(fields)}) values ({','.join(['%s'] * len(fields))}) on conflict(alignment_run_id) do nothing", tuple(fields.values()))
    saved = c.execute('select * from public.piano_alignment_runs where alignment_run_id=%s', (result['alignment_run_id'],)).fetchone()
    for key, value in fields.items():
        expected_value = value.obj if isinstance(value, Jsonb) else value
        if key == 'processing_purpose' and purpose == 'research':
            continue  # Reprocessing identical online content is an idempotent no-op.
        if saved[key] != expected_value and str(saved[key]) != str(expected_value):
            raise ValueError('Immutable run identity conflict: ' + key)
    return saved
