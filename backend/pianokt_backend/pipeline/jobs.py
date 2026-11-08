"""Batch materialization of immutable alignment-result objects.

``result.json`` is deliberately a rich replay artifact. This module turns it
into layer-appropriate data instead of copying that nested JSON into every
serving row:

* Bronze: raw result envelope, raw match envelopes, and browser key telemetry.
* Silver: normalized alignment events, including lineage JSON while the
  contract evolves.
* Gold: only quality-approved, typed serving facts.
"""

import json
import math
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from ..contracts import stable_id
from .tables import (
    ALIGNMENT_ATTEMPT_SCHEMA,
    BRONZE_MIDI_EVENT_SCHEMA,
    BRONZE_TRANSPORT_SEGMENT_SCHEMA,
    ENVELOPE_SCHEMA,
    GOLD_ALIGNMENT_EVENT_SCHEMA,
    ATTEMPT_EVENT_SCHEMA,
    LEGACY_SUMMARY_SCHEMA,
    SILVER_ALIGNMENT_EVENT_SCHEMA,
)


JAPAN_TIME_ZONE = ZoneInfo('Asia/Tokyo')


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _string(value):
    return None if value is None else str(value)


def _jst_timestamp(value):
    """Render an absolute ISO timestamp in Japan time without changing its instant.

    UTC remains the canonical storage format. These companion values make lake
    queries and exports deterministic for the product's Japanese operating
    calendar, rather than inheriting whichever time zone ran a query.
    """
    text = _string(value)
    if text is None:
        return None
    try:
        timestamp = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(JAPAN_TIME_ZONE).isoformat()


def _jst_date(value):
    timestamp = _jst_timestamp(value)
    return timestamp[:10] if timestamp else None


def _float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    # Do not silently truncate a non-integral JSON number.
    try:
        if float(value) != number:
            return None
    except (TypeError, ValueError):
        return None
    return number


def _boolean(value):
    return value if isinstance(value, bool) else None


def _first(mapping, *names):
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def _result_quality(result):
    """Return the explicit semantic quality decision for a result.

    A result written before the quality gate is *not* evidence that is safe to
    serve or train on. It remains in Bronze and can be replayed deliberately,
    but it must not silently enter Gold merely because the old payload lacked a
    field that did not exist yet.
    """

    diagnostics = result.get('diagnostics')
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    summary = result.get('summary')
    summary = summary if isinstance(summary, Mapping) else {}
    value = _first(result, 'alignment_quality_valid')
    if value is None:
        value = _first(diagnostics, 'alignment_quality_valid')
    if value is None:
        value = _first(summary, 'alignment_quality_valid')
    if value is None:
        valid = False
    elif isinstance(value, bool):
        valid = value
    else:
        valid = False

    reason = _first(result, 'alignment_quality_reason')
    if reason is None:
        reason = _first(diagnostics, 'alignment_quality_reason')
    if reason is None:
        reason = _first(summary, 'alignment_quality_reason')
    if not valid and not reason:
        reason = (
            'alignment_quality_not_valid'
            if isinstance(value, bool)
            else 'legacy_alignment_without_quality_gate'
        )
    return valid, _string(reason)


def _metadata(result):
    practice = result.get('practice_metadata')
    practice = practice if isinstance(practice, Mapping) else {}
    config = result.get('config')
    config = config if isinstance(config, Mapping) else {}
    attempt_created_at = _string(result.get('attempt_created_at'))
    alignment_completed_at = _string(result.get('alignment_completed_at'))
    return {
        'alignment_run_id': _string(result.get('alignment_run_id')),
        'attempt_id': _string(result.get('attempt_id')),
        'learner_key': _string(result.get('learner_key')),
        'song_id': _string(result.get('song_id')),
        'segment_id': _string(result.get('segment_id') or practice.get('segment_id')),
        'segment_index': _integer(result.get('segment_index') or practice.get('segment_index')),
        'midi_sha256': _string(result.get('midi_sha256') or practice.get('midi_sha256')),
        'session_id': _string(_first(result, 'session_id') or _first(practice, 'session_id')),
        'score_version': _string(result.get('score_version')),
        'performance_hash': _string(result.get('performance_hash')),
        'alignment_version': _string(_first(result, 'alignment_version') or _first(config, 'version')),
        'matcher_version': _string(result.get('matcher_version')),
        'attempt_created_at': attempt_created_at,
        'attempt_created_at_jst': _jst_timestamp(attempt_created_at),
        'attempt_date_jst': _jst_date(attempt_created_at),
        'alignment_completed_at': alignment_completed_at,
        'alignment_completed_at_jst': _jst_timestamp(alignment_completed_at),
        'alignment_completed_date_jst': _jst_date(alignment_completed_at),
    }


def _telemetry_rows(result, meta, source_key):
    """Normalize optional browser event/transport sidecars for Bronze.

    A missing sidecar is valid for historical attempts. A present but malformed
    sidecar is preserved in Bronze and causes a semantic quality rejection so
    it cannot become trusted Gold data.
    """

    errors = []
    raw_events = result.get('performance_events')
    if raw_events is None:
        raw_events = []
    if not isinstance(raw_events, list):
        return [], [], ['invalid_performance_events']

    event_rows = []
    seen_event_ids = set()
    previous_sequence = None
    previous_song_time = None
    previous_now = None
    for index, raw in enumerate(raw_events):
        if not isinstance(raw, Mapping):
            errors.append('invalid_performance_event')
            continue
        sequence = _integer(raw.get('sequence'))
        if sequence is None:
            errors.append('missing_midi_event_sequence')
            sequence = index
        # The browser protocol intentionally has a monotonically increasing
        # sequence rather than a UUID per key event. Use it as the stable
        # source identity when an older producer did not provide event_id.
        source_event_id = _string(raw.get('event_id')) or f'sequence:{sequence}'
        midi_event_id = stable_id(meta['attempt_id'], source_event_id)
        if midi_event_id in seen_event_ids:
            errors.append('duplicate_midi_event_id')
            continue
        seen_event_ids.add(midi_event_id)

        event_type = _string(_first(raw, 'event_type', 'note_type', 'type'))
        if event_type:
            event_type = event_type.lower()
        event_type = {
            'noteon': 'note_on',
            'on': 'note_on',
            'noteoff': 'note_off',
            'off': 'note_off',
        }.get(event_type, event_type)
        midi_timestamp = _float(_first(raw, 'midi_event_timestamp_ms', 'midiEventTimestamp'))
        song_time = _float(_first(raw, 'player_song_time_sec', 'playerSongTime'))
        performance_now = _float(
            _first(raw, 'performance_now_timestamp_ms', 'performanceNowTimestamp')
        )
        pitch = _integer(raw.get('pitch'))
        velocity = _integer(raw.get('velocity'))
        source_segment_id = _string(raw.get('transport_segment_id'))

        if event_type not in {'note_on', 'note_off'}:
            errors.append('invalid_midi_event_type')
        if midi_timestamp is None or midi_timestamp < 0:
            errors.append('invalid_midi_event_timestamp')
        if song_time is None or song_time < 0:
            errors.append('invalid_player_song_time')
        if performance_now is None or performance_now < 0:
            errors.append('invalid_performance_now_timestamp')
        if pitch is None or not 0 <= pitch <= 127:
            errors.append('invalid_midi_pitch')
        if velocity is None or not 0 <= velocity <= 127:
            errors.append('invalid_midi_velocity')
        segment_id = (
            stable_id(meta['attempt_id'], source_segment_id)
            if source_segment_id
            else None
        )
        if previous_sequence is not None and sequence <= previous_sequence:
            errors.append('non_monotonic_midi_event_sequence')
        if previous_song_time is not None and song_time is not None and song_time < previous_song_time:
            errors.append('non_monotonic_player_song_time')
        if previous_now is not None and performance_now is not None and performance_now < previous_now:
            errors.append('non_monotonic_performance_now_timestamp')
        previous_sequence = sequence
        previous_song_time = song_time
        previous_now = performance_now
        event_rows.append(
            dict(
                meta,
                midi_event_id=midi_event_id,
                source_event_id=source_event_id,
                event_type=event_type,
                timestamp_source=_string(_first(raw, 'timestamp_source', 'timestampSource')),
                transport_segment_id=segment_id,
                source_key=source_key,
                midi_event_timestamp_ms=midi_timestamp,
                player_song_time_sec=song_time,
                performance_now_timestamp_ms=performance_now,
                pitch=pitch,
                velocity=velocity,
                sequence=sequence,
                payload_json=_json(raw),
            )
        )

    raw_segments = result.get('transport_segments')
    if raw_segments is None:
        raw_segments = []
    if not isinstance(raw_segments, list):
        return event_rows, [], errors + ['invalid_transport_segments']
    segment_rows = []
    seen_segment_ids = set()
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, Mapping):
            errors.append('invalid_transport_segment')
            continue
        sequence = _integer(raw.get('sequence'))
        if sequence is None:
            errors.append('missing_transport_segment_sequence')
            sequence = index
        # Current browser segments are transport markers (start/resume/pause/
        # stop), identified by their sequence rather than an explicit UUID.
        source_segment_id = (
            _string(_first(raw, 'transport_segment_id', 'segment_id', 'id'))
            or f'sequence:{sequence}'
        )
        segment_id = stable_id(meta['attempt_id'], source_segment_id)
        if segment_id in seen_segment_ids:
            errors.append('duplicate_transport_segment_id')
            continue
        seen_segment_ids.add(segment_id)
        segment_type = _string(_first(raw, 'transport_event_type', 'event_type', 'type'))
        if segment_type:
            segment_type = segment_type.lower()
        segment_type = {
            'play_started': 'start',
            'resumed': 'resume',
            'paused': 'pause',
            'finished': 'stop',
            'exited': 'stop',
        }.get(segment_type, segment_type)
        song_time = _float(_first(raw, 'player_song_time_sec', 'playerSongTime'))
        performance_now = _float(
            _first(raw, 'performance_now_timestamp_ms', 'performanceNowTimestamp')
        )
        segment_rows.append(
            dict(
                meta,
                transport_segment_id=segment_id,
                source_key=source_key,
                sequence=sequence,
                transport_event_type=segment_type,
                player_song_time_sec=song_time,
                performance_now_timestamp_ms=performance_now,
                start_player_song_time_sec=_float(
                    _first(raw, 'start_player_song_time_sec', 'song_time_start_sec')
                ) or song_time,
                end_player_song_time_sec=_float(
                    _first(raw, 'end_player_song_time_sec', 'song_time_end_sec')
                ),
                start_performance_now_timestamp_ms=_float(
                    _first(raw, 'start_performance_now_timestamp_ms', 'performance_now_start_ms')
                ) or performance_now,
                end_performance_now_timestamp_ms=_float(
                    _first(raw, 'end_performance_now_timestamp_ms', 'performance_now_end_ms')
                ),
                payload_json=_json(raw),
            )
        )
    # A browser event has no explicit segment id. Associate it with the latest
    # start/resume marker in the same monotonic performance clock. Pause/stop
    # clear the active segment. The synthetic fallback makes old telemetry
    # queryable without pretending that a pause is musical time.
    boundaries = sorted(
        (
            row['performance_now_timestamp_ms'],
            row['sequence'],
            row['transport_segment_id'],
            row['transport_event_type'],
        )
        for row in segment_rows
        if row['performance_now_timestamp_ms'] is not None
    )
    boundary_index = 0
    active_segment_id = None
    for event in sorted(event_rows, key=lambda row: (row['performance_now_timestamp_ms'] is None, row['performance_now_timestamp_ms'], row['sequence'])):
        while (
            boundary_index < len(boundaries)
            and event['performance_now_timestamp_ms'] is not None
            and boundaries[boundary_index][0] <= event['performance_now_timestamp_ms']
        ):
            _, _, marker_id, marker_type = boundaries[boundary_index]
            if marker_type in {'start', 'resume'}:
                active_segment_id = marker_id
            elif marker_type in {'pause', 'stop'}:
                active_segment_id = None
            boundary_index += 1
        if event['transport_segment_id'] is None:
            event['transport_segment_id'] = active_segment_id or stable_id(
                meta['attempt_id'], 'unsegmented-performance-events'
            )
    return event_rows, segment_rows, errors


def _raw_match_envelopes(result, source_key):
    rows = {'score_notes': [], 'performance_notes': []}
    raw_matches = result.get('raw_matches')
    if not isinstance(raw_matches, list):
        return rows, ['invalid_raw_matches']
    errors = []
    for raw in raw_matches:
        if not isinstance(raw, Mapping):
            errors.append('invalid_raw_match')
            continue
        for side, table in [('ref', 'score_notes'), ('performance', 'performance_notes')]:
            note_id = raw.get(side + '_id')
            if note_id is None:
                continue
            identity = result.get('score_version') if side == 'ref' else result.get('attempt_id')
            key = stable_id(identity, note_id)
            rows[table].append(
                dict(
                    event_id=key,
                    event_type=table,
                    source_key=source_key,
                    payload_json=_json(
                        {key: value for key, value in raw.items() if key.startswith(side + '_')}
                    ),
                )
            )
    # One raw note can appear in multiple matcher rows. Keep merge input unique.
    for table in rows:
        rows[table] = list({row['event_id']: row for row in rows[table]}.values())
    return rows, errors


def _alignment_notes(result, meta, source_key, quality_valid, quality_reason):
    raw_notes = result.get('notes')
    if not isinstance(raw_notes, list):
        return [], ['invalid_notes']
    errors = []
    notes = []
    seen_ids = set()
    summary = result.get('summary')
    summary = summary if isinstance(summary, Mapping) else {}
    timing_default = _boolean(summary.get('timing_labels_valid'))
    for raw in raw_notes:
        if not isinstance(raw, Mapping):
            errors.append('invalid_note')
            continue
        label = _string(raw.get('alignment_type'))
        reject_reason = _integer(raw.get('reject_reason'))
        event_id = _string(raw.get('alignment_event_id'))
        if not event_id:
            errors.append('missing_alignment_event_id')
            continue
        if event_id in seen_ids:
            errors.append('duplicate_alignment_event_id')
            continue
        seen_ids.add(event_id)
        if label not in ('match', 'deletion', 'insertion'):
            errors.append('invalid_label')
        elif label == 'insertion' and reject_reason is not None:
            errors.append('insertion_has_target_label')
        elif label != 'insertion' and reject_reason not in range(5):
            errors.append('invalid_label')
        if label in ('match', 'deletion') and raw.get('ref_id') is None:
            errors.append('missing_reference')
        if label in ('match', 'insertion') and raw.get('performance_id') is None:
            errors.append('missing_performance')

        ref_onset = _float(raw.get('ref_onset_sec'))
        performance_onset = _float(raw.get('performance_onset_sec'))
        raw_timing = _float(
            _first(raw, 'raw_timing_deviation_sec', 'timing_deviation_sec')
        )
        if raw_timing is None and ref_onset is not None and performance_onset is not None:
            raw_timing = performance_onset - ref_onset
        calibration = _float(raw.get('calibration_offset_sec'))
        local_timing = _float(_first(raw, 'local_timing_deviation_sec', 'timing_deviation_local_sec'))
        if local_timing is None and raw_timing is not None and calibration is not None:
            local_timing = raw_timing - calibration
        timing_deviation = _float(raw.get('timing_deviation_sec'))
        if timing_deviation is None:
            timing_deviation = raw_timing
        timing_labels_valid = _boolean(raw.get('timing_labels_valid'))
        if timing_labels_valid is None:
            timing_labels_valid = timing_default
        if not quality_valid:
            timing_labels_valid = False

        notes.append(
            dict(
                meta,
                alignment_event_id=event_id,
                alignment_type=label,
                ref_id=_string(raw.get('ref_id')),
                performance_id=_string(raw.get('performance_id')),
                ref_pitch=_float(raw.get('ref_pitch')),
                performance_pitch=_float(raw.get('performance_pitch')),
                ref_onset_sec=ref_onset,
                performance_onset_sec=performance_onset,
                performance_player_song_time_sec=_float(
                    raw.get('performance_player_song_time_sec')
                ),
                performance_onset_sec_global_aligned=_float(raw.get('performance_onset_sec_global_aligned')),
                timing_deviation_global_aligned=_float(raw.get('timing_deviation_global_aligned')),
                raw_timing_deviation_sec=raw_timing,
                calibration_offset_sec=calibration,
                local_timing_deviation_sec=local_timing,
                timing_deviation_sec=timing_deviation,
                reject_reason=reject_reason,
                is_correct=_boolean(raw.get('is_correct')),
                within_observed_range=_boolean(raw.get('within_observed_range')),
                timing_labels_valid=timing_labels_valid,
                alignment_quality_valid=quality_valid,
                alignment_quality_reason=quality_reason,
                source_key=source_key,
                payload_json=_json(raw),
            )
        )
    return notes, errors


def _attempt_event_rows(result, meta):
    data = result.get('events_data')
    if not isinstance(data, Mapping):
        return [], ['invalid_events_data']
    names = ('onset_seconds', 'pitches', 'intervals_seconds', 'kc_vectors', 'timing_offset_ms', 'pitch_offset_semitones', 'extra_notes')
    reasons = data.get('reject_reason')
    if not isinstance(reasons, Mapping) or not isinstance(reasons.get('event'), list) or not isinstance(reasons.get('note'), list):
        return [], ['invalid_event_reject_reason']
    arrays = [data.get(name) for name in names] + [reasons['event'], reasons['note']]
    if any(not isinstance(value, list) for value in arrays):
        return [], ['invalid_event_arrays']
    lengths = {len(value) for value in arrays}
    if len(lengths) != 1:
        return [], ['event_array_length']
    rows, errors = [], []
    for index in range(len(reasons['event'])):
        pitches = data['pitches'][index]
        note = reasons['note'][index]
        timing = data['timing_offset_ms'][index]
        delta = data['pitch_offset_semitones'][index]
        if (not isinstance(pitches, list) or len(pitches) != 2 or not all(isinstance(hand, list) for hand in pitches)
                or not all(isinstance(item, list) and len(item) == 2 and all(
                    isinstance(item[hand], list) and len(item[hand]) == len(pitches[hand])
                    for hand in (0, 1)
                ) for item in (note, timing, delta))):
            errors.append('invalid_event_note_shape'); continue
        if any(code not in range(5) for hand in note for code in hand) or data['reject_reason']['event'][index] not in (0, 1):
            errors.append('invalid_event_label'); continue
        derived = int(any(code != 0 for hand in note for code in hand) or bool(data['extra_notes'][index]))
        if derived != data['reject_reason']['event'][index]:
            errors.append('invalid_event_derivation'); continue
        rows.append(dict(meta, event_id=stable_id(meta['alignment_run_id'], index), event_index=index,
            onset_seconds=_float(data['onset_seconds'][index]), pitches=pitches,
            intervals_seconds=data['intervals_seconds'][index], kc_vector_json=_json(data['kc_vectors'][index]),
            reject_reason_event=data['reject_reason']['event'][index], reject_reason_note_json=_json(note),
            timing_offset_ms_json=_json(timing), pitch_offset_semitones_json=_json(delta),
            extra_notes=data['extra_notes'][index]))
    return rows, errors


def _attempt_summary(result, meta, quality_valid, quality_reason):
    summary = result.get('summary')
    summary = summary if isinstance(summary, Mapping) else {}
    diagnostics = result.get('diagnostics')
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    context = result.get('alignment_context')
    context = context if isinstance(context, Mapping) else {}
    return dict(
        meta,
        expected_notes=_integer(summary.get('expected_notes')),
        correct_notes=_integer(summary.get('correct_notes')),
        extra_notes=_integer(summary.get('extra_notes')),
        missing_notes=_integer(summary.get('missing_notes')),
        accuracy=_float(summary.get('accuracy')),
        timing_labels_valid=_boolean(summary.get('timing_labels_valid')),
        alignment_quality_valid=quality_valid,
        alignment_quality_reason=quality_reason,
        anchors_total=_integer(diagnostics.get('anchors_total')),
        anchors_kept=_integer(diagnostics.get('anchors_kept')),
        global_offset_sec=_float(diagnostics.get('global_offset_sec')),
        offset_mad_sec=_float(_first(diagnostics, 'offset_mad_sec', 'mad_sec')),
        reference_time_origin_sec=_float(context.get('reference_origin_sec')),
        observed_end_sec=_float(context.get('observed_end_sec')),
        time_basis=_string(context.get('time_basis')),
    )


def _quality_envelope(run, source_key, event_type, **payload):
    return dict(
        event_id=run,
        event_type=event_type,
        source_key=source_key,
        payload_json=_json(payload),
    )


def publish_alignment(result, tables, source_key):
    run = result['alignment_run_id']
    meta = _metadata(result)
    if not meta['attempt_id'] or not meta['alignment_run_id']:
        raise ValueError('Alignment result lacks identity')
    envelope = dict(
        event_id=run,
        event_type='alignment.completed',
        source_key=source_key,
        payload_json=_json(result),
    )

    # A committed run is immutable across every projection, including replay.
    # Changing a materializer requires a new lake namespace/version, not an
    # in-place rewrite visible to readers halfway through a multi-table update.
    for committed in tables.rows('ops/completed_runs'):
        if committed['event_id']==run:
            original=next((r for r in tables.rows('bronze/alignment_runs') if r['event_id']==run),None)
            if original is None or original['payload_json']!=envelope['payload_json']:
                raise ValueError('Committed alignment run identity conflict')
            return

    # Bronze is always written first, even when the canonical result is later
    # quarantined. This is the audit/replay source of truth.
    tables.merge('bronze/alignment_runs', [envelope], ENVELOPE_SCHEMA, 'event_id')
    raw_rows, raw_errors = _raw_match_envelopes(result, source_key)
    for name, rows in raw_rows.items():
        tables.merge('bronze/' + name, rows, ENVELOPE_SCHEMA, 'event_id')
    event_rows, segment_rows, telemetry_errors = _telemetry_rows(result, meta, source_key)
    tables.merge('bronze/midi_events', event_rows, BRONZE_MIDI_EVENT_SCHEMA, 'midi_event_id')
    tables.merge(
        'bronze/transport_segments',
        segment_rows,
        BRONZE_TRANSPORT_SEGMENT_SCHEMA,
        'transport_segment_id',
    )

    quality_valid, quality_reason = _result_quality(result)
    # Telemetry is optional only for historical immutable results. If a new
    # sidecar is present but malformed, do not let it support trusted facts.
    if telemetry_errors:
        quality_valid = False
        telemetry_reason = 'invalid_performance_telemetry:' + ','.join(sorted(set(telemetry_errors)))
        quality_reason = ';'.join(
            reason for reason in (quality_reason, telemetry_reason) if reason and reason != 'ok'
        ) or telemetry_reason

    notes, note_errors = _alignment_notes(
        result, meta, source_key, quality_valid, quality_reason
    )
    attempt_events, event_errors = _attempt_event_rows(result, meta)
    errors = sorted(set(raw_errors + note_errors + event_errors))
    if errors:
        tables.merge(
            'quality/alignment_runs',
            [_quality_envelope(run, source_key, 'alignment.materialization_failed', errors=errors)],
            ENVELOPE_SCHEMA,
            'event_id',
        )
        raise ValueError('Alignment materialization validation failed')

    # Silver preserves normalized output and lineage even for a low-quality
    # alignment, so the failure can be debugged/replayed without reopening raw
    # MIDI. Gold below is deliberately quality-gated.
    tables.merge(
        'silver/midi_alignment_events',
        notes,
        SILVER_ALIGNMENT_EVENT_SCHEMA,
        'alignment_event_id',
    )

    if not quality_valid:
        tables.merge(
            'quality/alignment_runs',
            [
                _quality_envelope(
                    run,
                    source_key,
                    'alignment.quality_rejected',
                    alignment_quality_valid=False,
                    alignment_quality_reason=quality_reason,
                    telemetry_errors=sorted(set(telemetry_errors)),
                )
            ],
            ENVELOPE_SCHEMA,
            'event_id',
        )
        # This is a terminal, known-quality outcome rather than a transient
        # materialization error. Commit it last so catch_up does not retry it.
        tables.merge(
            'ops/completed_runs',
            [
                _quality_envelope(
                    run,
                    source_key,
                    'alignment.quality_rejected',
                    alignment_quality_valid=False,
                    alignment_quality_reason=quality_reason,
                )
            ],
            ENVELOPE_SCHEMA,
            'event_id',
        )
        return

    gold_notes = [
        {key: value for key, value in note.items() if key not in {'source_key', 'payload_json'}}
        for note in notes
    ]
    tables.merge(
        'gold/fact_midi_alignment',
        gold_notes,
        GOLD_ALIGNMENT_EVENT_SCHEMA,
        'alignment_event_id',
    )
    tables.merge('gold/attempt_events', attempt_events, ATTEMPT_EVENT_SCHEMA, 'event_id')
    attempt_summary = _attempt_summary(result, meta, quality_valid, quality_reason)
    tables.merge(
        'gold/fact_alignment_attempt',
        [attempt_summary],
        ALIGNMENT_ATTEMPT_SCHEMA,
        # Keep the previous committed run visible until this run commits.
        'alignment_run_id',
    )
    # Preserve the old serving table while readers migrate to the explicit
    # alignment-attempt fact.
    tables.merge(
        'gold/fact_practice_attempt',
        [attempt_summary],
        LEGACY_SUMMARY_SCHEMA,
        'alignment_run_id',
    )
    # Commit manifest last; a Delta transaction is per table, not across this
    # function.
    tables.merge('ops/completed_runs', [envelope], ENVELOPE_SCHEMA, 'event_id')


def catch_up(objects, tables, replay=False):
    completed = set() if replay else {row['event_id'] for row in tables.rows('ops/completed_runs')}
    failures = []
    count = 0
    for key in objects.keys('alignment'):
        if not key.endswith('/result.json'):
            continue
        # Deterministic result keys allow skipping committed objects before download.
        if key.split('/')[-2] in completed:
            continue
        try:
            result = json.loads(objects.read(key))
            if result['alignment_run_id'] in completed:
                continue
            publish_alignment(result, tables, key)
            count += 1
        except Exception as exc:
            failures.append(key)
            tables.merge(
                'ops/failures',
                [
                    dict(
                        event_id=stable_id(key),
                        event_type='materialization.failed',
                        source_key=key,
                        payload_json=_json({'error_type': type(exc).__name__}),
                    )
                ],
                ENVELOPE_SCHEMA,
                'event_id',
            )
    if failures:
        raise RuntimeError(f'{len(failures)} failed runs; see ops/failures')
    return count
