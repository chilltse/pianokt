"""Schemas and idempotent Delta-table helpers for the lakehouse layers.

The schemas deliberately make the raw/canonical/serving boundary explicit:

* Bronze keeps immutable source envelopes, including the browser's per-key MIDI
  telemetry.  ``payload_json`` is retained here as the lossless source record.
* Silver contains normalized alignment events.  It may retain the original
  per-event JSON while the alignment contract is still evolving.
* Gold contains only typed, queryable facts.  In particular it must never
  duplicate the per-note JSON payload stored upstream.
"""

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError


def _strings(*names: str) -> list[tuple[str, pa.DataType]]:
    return [(name, pa.string()) for name in names]


def _floats(*names: str) -> list[tuple[str, pa.DataType]]:
    return [(name, pa.float64()) for name in names]


ENVELOPE_SCHEMA = pa.schema(
    _strings('event_id', 'event_type', 'source_key', 'payload_json')
)

# Browser-side source-of-truth key telemetry. Timestamps use explicit units:
# MIDI and performance clocks are milliseconds; song time is seconds.
BRONZE_MIDI_EVENT_SCHEMA = pa.schema(
    _strings(
        'midi_event_id',
        'source_event_id',
        'alignment_run_id',
        'attempt_id',
        'learner_key',
        'song_id',
        'session_id',
        'event_type',
        'timestamp_source',
        'transport_segment_id',
        'source_key',
        'payload_json',
    )
    + _floats(
        'midi_event_timestamp_ms',
        'player_song_time_sec',
        'performance_now_timestamp_ms',
    )
    + [('pitch', pa.int32()), ('velocity', pa.int32()), ('sequence', pa.int64())]
)

# Segments make pause/resume boundaries auditable without interpreting elapsed
# wall-clock time as musical time. The exact browser payload remains in
# ``payload_json`` so new transport fields do not require a raw-data migration.
BRONZE_TRANSPORT_SEGMENT_SCHEMA = pa.schema(
    _strings(
        'transport_segment_id',
        'alignment_run_id',
        'attempt_id',
        'learner_key',
        'song_id',
        'session_id',
        'transport_event_type',
        'source_key',
        'payload_json',
    )
    + _floats(
        'player_song_time_sec',
        'performance_now_timestamp_ms',
        'start_player_song_time_sec',
        'end_player_song_time_sec',
        'start_performance_now_timestamp_ms',
        'end_performance_now_timestamp_ms',
    )
    + [('sequence', pa.int64())]
)

_ALIGNMENT_EVENT_STRING_FIELDS = _strings(
    'alignment_event_id',
    'alignment_run_id',
    'attempt_id',
    'learner_key',
    'song_id',
    'session_id',
    'score_version',
    'performance_hash',
    'alignment_version',
    'matcher_version',
    'alignment_type',
    'ref_id',
    'performance_id',
    'alignment_completed_at',
    'alignment_quality_reason',
)
_ALIGNMENT_EVENT_FLOAT_FIELDS = _floats(
    'ref_pitch',
    'performance_pitch',
    'ref_onset_sec',
    'performance_onset_sec',
    # Absolute song time emitted by the browser. ``performance_onset_sec`` is
    # snapshot-relative so it can be compared directly with reference MIDI;
    # retaining both makes a cropped range fully reconstructable in facts.
    'performance_player_song_time_sec',
    # Legacy global-offset fields are retained for audit/replay only. New
    # quality-aware alignment should use the raw/local timing fields below.
    'performance_onset_sec_global_aligned',
    'timing_deviation_global_aligned',
    'raw_timing_deviation_sec',
    'calibration_offset_sec',
    'local_timing_deviation_sec',
    'timing_deviation_sec',
)
_ALIGNMENT_EVENT_TAIL_FIELDS = [
    ('reject_reason', pa.int32()),
    ('is_correct', pa.bool_()),
    ('within_observed_range', pa.bool_()),
    ('timing_labels_valid', pa.bool_()),
    ('alignment_quality_valid', pa.bool_()),
]

# Silver is the canonical, normalized alignment layer. Keeping the source
# per-note JSON here makes it possible to replay a contract change without
# treating duplicated JSON as an analytics-serving fact.
SILVER_ALIGNMENT_EVENT_SCHEMA = pa.schema(
    _ALIGNMENT_EVENT_STRING_FIELDS
    + _ALIGNMENT_EVENT_FLOAT_FIELDS
    + _ALIGNMENT_EVENT_TAIL_FIELDS
    + _strings('source_key', 'payload_json')
)

# One row per alignment event; deliberately no payload_json.
GOLD_ALIGNMENT_EVENT_SCHEMA = pa.schema(
    _ALIGNMENT_EVENT_STRING_FIELDS
    + _ALIGNMENT_EVENT_FLOAT_FIELDS
    + _ALIGNMENT_EVENT_TAIL_FIELDS
)

# Backwards-compatible alias for consumers that imported the old name. New
# code should use the explicit Silver/Gold schemas above.
NOTE_SCHEMA = SILVER_ALIGNMENT_EVENT_SCHEMA

HAND_SCHEMA = pa.schema(
    _strings('event_id', 'alignment_run_id', 'attempt_id', 'learner_key', 'song_id', 'hand')
    + [
        ('reject_reason', pa.int32()),
        ('timing_offset', pa.float64()),
        ('pitch_offset', pa.float64()),
        ('duration', pa.float64()),
        ('onset_time', pa.float64()),
        ('pitches', pa.list_(pa.int32())),
    ]
)

# The old serving table is intentionally retained while downstream readers are
# migrated. ``fact_alignment_attempt`` below is the canonical new table.
LEGACY_SUMMARY_SCHEMA = pa.schema(
    _strings(
        'alignment_run_id',
        'attempt_id',
        'learner_key',
        'song_id',
        'score_version',
        'alignment_version',
    )
    + [
        ('expected_notes', pa.int64()),
        ('correct_notes', pa.int64()),
        ('extra_notes', pa.int64()),
        ('missing_notes', pa.int64()),
    ]
    + [('accuracy', pa.float64()), ('timing_labels_valid', pa.bool_())]
)
SUMMARY_SCHEMA = LEGACY_SUMMARY_SCHEMA

ALIGNMENT_ATTEMPT_SCHEMA = pa.schema(
    _strings(
        'alignment_run_id',
        'attempt_id',
        'learner_key',
        'song_id',
        'session_id',
        'score_version',
        'alignment_version',
        'matcher_version',
        'alignment_completed_at',
        'alignment_quality_reason',
        'time_basis',
    )
    + [
        ('expected_notes', pa.int64()),
        ('correct_notes', pa.int64()),
        ('extra_notes', pa.int64()),
        ('missing_notes', pa.int64()),
        ('anchors_total', pa.int64()),
        ('anchors_kept', pa.int64()),
    ]
    + [
        ('accuracy', pa.float64()),
        ('global_offset_sec', pa.float64()),
        ('offset_mad_sec', pa.float64()),
        ('reference_time_origin_sec', pa.float64()),
        ('observed_end_sec', pa.float64()),
    ]
    + [('timing_labels_valid', pa.bool_()), ('alignment_quality_valid', pa.bool_())]
)


class Tables:
    def __init__(self, root):
        self.root = root.rstrip('/')

    def merge(self, name, rows, schema, key):
        if not rows:
            return
        source = pa.Table.from_pylist(rows, schema=schema)
        if len(set(source[key].to_pylist())) != len(rows):
            raise ValueError('Duplicate source keys')
        path = self.root + '/' + name
        try:
            table = DeltaTable(path)
        except TableNotFoundError:
            write_deltalake(path, source, mode='error')
            return
        # New canonical fields are additive. This lets the pipeline process
        # old Gold/Silver tables during a rolling deployment. Dropping the
        # historical Gold payload_json column still requires a one-off Delta
        # table rewrite; new rows never populate it.
        (
            table.merge(
                source,
                predicate=f't.{key}=s.{key}',
                source_alias='s',
                target_alias='t',
                merge_schema=True,
            )
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute()
        )

    def rows(self, name):
        try:
            return DeltaTable(self.root + '/' + name).to_pyarrow_table().to_pylist()
        except TableNotFoundError:
            return []
