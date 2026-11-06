"""Time-guided alignment service for challenge recordings.

The public result shape is intentionally compatible with the existing worker,
hand-event builder, and lake pipeline.  The matching semantics are not: a
challenge attempt is matched inside its transport-time window, rather than by
an unconstrained full-score DTW search.
"""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
import json
from collections import Counter
from math import isfinite
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import labels as note_labels, events
from .feature_extraction import resolve_kc_version, extract_reference_features
from .serialization import alignment_records
from .hands import parse_midi_song_for_hand_inference, parser_infer_hands
from .time_guided import (
    align_chords,
    group_chords,
    normalise_player_song_time,
    performance_notes_from_events,
    read_midi_notes,
    rows_from_operations,
    validate_transport_segments,
)
from ..contracts import AlignmentConfig, check_midi, digest, stable_id


MATCHER_VERSION = "time-guided-chord-monotonic-v3"


class InsufficientAnchors(ValueError):
    """A MIDI-only recording has too little evidence for safe calibration."""


def matcher_version() -> str:
    """Matcher identity used by result IDs and worker crash recovery."""

    return MATCHER_VERSION


def _canonical_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical_json(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical_json(item) for item in value]
    if isinstance(value, float):
        return round(value, 9) if isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _context_identity(
    performance_events: Any,
    observed_end_sec: float | None,
    transport_segments: Any,
    reference_origin_sec: float,
) -> str:
    context = _canonical_json(
        {
            "performance_events": performance_events,
            "observed_end_sec": observed_end_sec,
            "transport_segments": transport_segments,
            "reference_origin_sec": reference_origin_sec,
        }
    )
    return digest(json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def alignment_run_id(
    reference: Path,
    performance: Path,
    attempt_id: str,
    config: AlignmentConfig = AlignmentConfig(),
    performance_events: Any = None,
    observed_end_sec: float | None = None,
    transport_segments: Any = None,
    reference_origin_sec: float = 0.0,
    *, kc_labeler_version: str | None = None,
) -> str:
    """Return a stable result ID, including time context that changes output."""

    if transport_segments is None and isinstance(performance_events, dict):
        transport_segments = performance_events.get("transportSegments")

    return stable_id(
        attempt_id,
        digest(reference.read_bytes()),
        digest(performance.read_bytes()),
        config.identity,
        matcher_version(),
        resolve_kc_version(kc_labeler_version),
        # Output-contract identity: old immutable results lack event source IDs
        # and may serialize integer-domain fields as floats. Do not reuse them.
        "event-source-ids-nullable-integers-attempt-context",
        _context_identity(
            performance_events,
            observed_end_sec,
            transport_segments,
            reference_origin_sec,
        ),
    )


def _apply_timing(frame: pd.DataFrame, calibration_offset_sec: float) -> pd.DataFrame:
    """Add raw and legacy-compatible timing fields without losing raw timing."""

    df = frame.copy()
    reference = pd.to_numeric(df["ref_onset_sec"], errors="coerce")
    performance = pd.to_numeric(df["performance_onset_sec"], errors="coerce")
    df["raw_timing_deviation_sec"] = performance - reference
    df["calibration_offset_sec"] = calibration_offset_sec
    aligned = performance - calibration_offset_sec
    # Existing consumers expect these names. In player-song-time mode they are
    # deliberately aliases for raw values because calibration is zero.
    df["performance_onset_sec_global_aligned"] = aligned
    df["timing_deviation_global_aligned"] = aligned - reference
    df["local_timing_deviation_sec"] = df["timing_deviation_global_aligned"]
    return df


def correct_offset(
    frame: pd.DataFrame,
    minimum_anchors: int = 3,
    max_mad_sec: float = 0.5,
    max_offset_sec: float = 1.0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply bounded calibration only for legacy MIDI-only attempts.

    A large MAD must never expand an outlier cutoff enough to legitimise a
    many-second score jump. On failure this returns raw timing and an explicit
    quality failure instead of shifting the performance by a bogus median.
    """

    df = frame.copy()
    anchors = df.loc[
        (df.alignment_type == "match")
        & df.ref_onset_sec.notna()
        & df.performance_onset_sec.notna()
        & (df.ref_pitch == df.performance_pitch)
    ].copy()
    anchors["group"] = anchors.ref_onset_sec.round(3)
    grouped = anchors.groupby("group")[["ref_onset_sec", "performance_onset_sec"]].median()
    if len(grouped) < minimum_anchors:
        raise InsufficientAnchors("Too few distinct matched onsets")
    delta = (grouped.performance_onset_sec - grouped.ref_onset_sec).to_numpy(dtype=float)
    median = float(np.median(delta))
    mad = float(np.median(np.abs(delta - median)))
    # Robust filtering may remove a real outlier, but its range is bounded so
    # huge dispersion cannot manufacture an enormous acceptance window.
    robust_limit = max(0.05, min(0.25, 3 * 1.4826 * mad))
    kept = delta[np.abs(delta - median) <= robust_limit]
    diagnostics: dict[str, Any] = {
        "anchors_total": int(len(delta)),
        "anchors_kept": int(len(kept)),
        "offset_median_sec": median,
        "mad_sec": mad,
        "calibration_max_mad_sec": max_mad_sec,
        "calibration_max_offset_sec": max_offset_sec,
    }
    if len(kept) < minimum_anchors:
        reason = "insufficient_reliable_calibration_anchors"
    elif mad > max_mad_sec:
        reason = "calibration_anchor_dispersion_too_large"
    else:
        offset = float(np.median(kept))
        reason = "calibration_offset_out_of_bounds" if abs(offset) > max_offset_sec else None
    if reason:
        diagnostics.update(
            global_offset_sec=0.0,
            calibration_offset_sec=0.0,
            alignment_quality_valid=False,
            alignment_quality_reason=reason,
        )
        return _apply_timing(df, 0.0), diagnostics
    diagnostics.update(
        global_offset_sec=offset,
        calibration_offset_sec=offset,
        alignment_quality_valid=True,
        alignment_quality_reason="ok",
    )
    return _apply_timing(df, offset), diagnostics


def configured_labels(config: AlignmentConfig):
    return SimpleNamespace(
        KC_FEATURE_NAMES=events.KC_FEATURE_NAMES,
        combine_reject_reasons=note_labels.combine_reject_reasons,
        build_attempt_events_data=partial(events.build_attempt_events_data,
            timing_threshold_ms=config.timing_threshold_ms,insertion_attach_window_sec=config.insertion_attach_window_sec),
        classify_matched_note=partial(note_labels.classify_matched_note,timing_threshold_ms=config.timing_threshold_ms),
    )


_RAW_COLUMNS = [
    "alignment_type", "ref_id", "performance_id", "ref_pitch", "performance_pitch", "ref_onset_sec",
    "performance_onset_sec", "performance_player_song_time_sec", "ref_duration_sec",
    "performance_duration_sec", "ref_track", "ref_onset_tick", "ref_channel",
    "ref_velocity", "performance_track", "performance_onset_tick",
    "performance_channel", "performance_velocity", "reference_chord_index",
    "performance_chord_index",
]


def _finite_nonnegative(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) and result >= 0 else None


def _telemetry_midi_integrity(telemetry_notes, midi_notes, tolerance_sec: float) -> dict[str, Any]:
    """Check that the uploaded MIDI and sidecar describe the same take."""

    result = {
        "performance_telemetry_note_count": len(telemetry_notes),
        "performance_midi_note_count": len(midi_notes),
        "telemetry_midi_onset_tolerance_sec": tolerance_sec,
        "performance_telemetry_midi_match": True,
        "performance_telemetry_midi_mismatch_index": None,
    }
    if len(telemetry_notes) != len(midi_notes):
        result.update(
            performance_telemetry_midi_match=False,
            performance_telemetry_midi_mismatch_index=min(len(telemetry_notes), len(midi_notes)),
        )
        return result
    # A chord has no meaningful internal note ordering: a student may press E
    # then C while a MIDI parser presents same-tick notes as C then E. Compare
    # onset buckets and pitch multisets instead of zip-ordering individual
    # notes, otherwise a perfectly valid chord is incorrectly quarantined.
    telemetry_chords = group_chords(telemetry_notes, tolerance_sec)
    midi_chords = group_chords(midi_notes, tolerance_sec)
    if len(telemetry_chords) != len(midi_chords):
        result.update(
            performance_telemetry_midi_match=False,
            performance_telemetry_midi_mismatch_index=min(len(telemetry_chords), len(midi_chords)),
        )
        return result
    for index, (telemetry, midi) in enumerate(zip(telemetry_chords, midi_chords)):
        if (
            Counter(note.pitch for note in telemetry.notes)
            != Counter(note.pitch for note in midi.notes)
            or abs(telemetry.onset_sec - midi.onset_sec) > tolerance_sec
        ):
            result.update(
                performance_telemetry_midi_match=False,
                performance_telemetry_midi_mismatch_index=index,
            )
            break
    return result


def _quality(
    *,
    audit_supplied: bool,
    invalid_events: int,
    invalid_transport_segments: int,
    non_monotonic: bool,
    telemetry_midi_match: bool,
    observed_end_source: str,
    reference_chords: list,
    legacy_calibration: dict[str, Any] | None,
) -> tuple[bool, str]:
    reasons: list[str] = []
    if not reference_chords:
        reasons.append("empty_observed_reference")
    if non_monotonic:
        reasons.append("non_monotonic_transport")
    if audit_supplied and invalid_events:
        reasons.append("invalid_performance_event_audit")
    if invalid_transport_segments:
        reasons.append("invalid_transport_segment_audit")
    if audit_supplied and not telemetry_midi_match:
        reasons.append("performance_telemetry_midi_mismatch")
    if not audit_supplied and observed_end_source == "unknown":
        reasons.append("missing_observed_window_and_event_audit")
    if legacy_calibration and not legacy_calibration.get("alignment_quality_valid", False):
        reasons.append(str(legacy_calibration.get("alignment_quality_reason", "legacy_calibration_unreliable")))
    return not reasons, "ok" if not reasons else ";".join(dict.fromkeys(reasons))


def align(
    reference: Path,
    performance: Path,
    attempt_id: str,
    config: AlignmentConfig = AlignmentConfig(),
    stage_sink=None,
    performance_events: Any = None,
    observed_end_sec: float | None = None,
    transport_segments: Any = None,
    reference_origin_sec: float = 0.0,
    *, kc_labeler_version: str | None = None,
):
    """Time-guided, monotonic challenge alignment.

    ``performance_events`` may be the browser audit object or its event list.
    Browser ``playerSongTime`` / ``player_song_time_sec`` is absolute song
    time; ``reference_origin_sec`` converts it to the snapshot-relative score
    time used by reference MIDI. ``observed_end_sec`` is already relative.
    """

    kc_labeler_version = resolve_kc_version(kc_labeler_version)
    for path in (reference, performance):
        check_midi(path)
    reference_all = read_midi_notes(str(reference))
    performance_midi = read_midi_notes(str(performance))
    if len(reference_all) > config.max_notes or len(performance_midi) > config.max_notes:
        raise ValueError("MIDI contains too many notes")
    origin = _finite_nonnegative(reference_origin_sec)
    if origin is None:
        raise ValueError("reference_origin_sec must be a finite non-negative number")

    audit = performance_notes_from_events(performance_events, config.transport_monotonic_tolerance_sec)
    if transport_segments is None and isinstance(performance_events, dict):
        transport_segments = performance_events.get("transportSegments")
    transport_supplied, invalid_transport_segments, non_monotonic_segments = validate_transport_segments(
        transport_segments,
        config.transport_monotonic_tolerance_sec,
        require_complete_cycle=audit.supplied,
    )
    if audit.supplied:
        # Keep player-song-time absolute in the event audit; use this shifted
        # copy only for reference-snapshot-relative matching.
        performance_notes = normalise_player_song_time(audit.notes, origin)
        time_basis = "player_song_time"
        integrity = _telemetry_midi_integrity(
            performance_notes, performance_midi, config.telemetry_midi_onset_tolerance_sec
        )
    else:
        performance_notes = performance_midi
        time_basis = "performance_midi_fallback"
        integrity = {
            "performance_telemetry_note_count": None,
            "performance_midi_note_count": len(performance_midi),
            "telemetry_midi_onset_tolerance_sec": config.telemetry_midi_onset_tolerance_sec,
            "performance_telemetry_midi_match": True,
            "performance_telemetry_midi_mismatch_index": None,
        }

    explicit_end = _finite_nonnegative(observed_end_sec)
    if observed_end_sec is not None and explicit_end is None:
        raise ValueError("observed_end_sec must be a finite non-negative number")
    if explicit_end is not None:
        observed_end, end_source = explicit_end, "provided"
    elif performance_notes:
        # Old attempts must still be local, never all-score DTW.
        observed_end = max(note.onset_sec for note in performance_notes)
        end_source = "performance_last_note_fallback"
    else:
        observed_end, end_source = 0.0, "unknown"

    reference_notes = [
        note for note in reference_all
        if note.onset_sec <= observed_end + config.chord_onset_tolerance_sec
    ]
    reference_chords = group_chords(reference_notes, config.chord_onset_tolerance_sec)
    performance_chords = group_chords(performance_notes, config.chord_onset_tolerance_sec)
    non_monotonic_transport = audit.non_monotonic_transport or non_monotonic_segments
    if non_monotonic_transport:
        # Never sort a loop/seek into a plausible-looking linear attempt.
        operations = [("deletion", index, None) for index in range(len(reference_chords))]
        operations.extend(("insertion", None, index) for index in range(len(performance_chords)))
        matching_method = "rejected_non_monotonic_transport"
    elif not reference_chords:
        operations = [("insertion", None, index) for index in range(len(performance_chords))]
        matching_method = "empty_observed_reference"
    else:
        operations, matching_method = align_chords(
            reference_chords,
            performance_chords,
            match_window_sec=config.match_window_sec,
            wrong_pitch_window_sec=config.wrong_pitch_window_sec,
            max_alignment_cells=config.max_alignment_cells,
        )

    raw = pd.DataFrame(
        rows_from_operations(reference_chords, performance_chords, operations),
        columns=_RAW_COLUMNS,
    )
    raw_records = alignment_records(raw)
    if audit.supplied:
        # This is the essential semantics change: do not calibrate away a
        # student's initial latency, dragging, or anticipatory playing.
        df = _apply_timing(raw, 0.0)
        calibration = {
            "anchors_total": 0, "anchors_kept": 0, "offset_median_sec": 0.0,
            "mad_sec": None, "global_offset_sec": 0.0, "calibration_offset_sec": 0.0,
            "alignment_quality_valid": True,
            "alignment_quality_reason": "not_applied_player_song_time",
            "offset_strategy": "not_applied_player_song_time",
        }
    else:
        try:
            df, calibration = correct_offset(
                raw, config.minimum_anchors, config.calibration_max_mad_sec,
                config.calibration_max_offset_sec,
            )
            calibration["offset_strategy"] = "bounded_legacy_calibration"
        except InsufficientAnchors:
            df = _apply_timing(raw, 0.0)
            calibration = {
                "anchors_total": 0, "anchors_kept": 0, "offset_median_sec": None,
                "mad_sec": None, "global_offset_sec": 0.0, "calibration_offset_sec": 0.0,
                "alignment_quality_valid": False,
                "alignment_quality_reason": "insufficient_legacy_calibration_anchors",
                "offset_strategy": "not_applied_insufficient_anchors",
            }

    matched = df.loc[
        (df.alignment_type == "match") & df.ref_onset_sec.notna() & df.performance_onset_sec.notna()
    ]
    deviations = pd.to_numeric(matched.raw_timing_deviation_sec, errors="coerce").dropna()
    raw_median = float(deviations.median()) if len(deviations) else None
    raw_mad = float(np.median(np.abs(deviations.to_numpy(dtype=float) - raw_median))) if raw_median is not None else None
    quality_valid, quality_reason = _quality(
        audit_supplied=audit.supplied,
        invalid_events=audit.invalid_event_count,
        invalid_transport_segments=invalid_transport_segments,
        non_monotonic=non_monotonic_transport,
        telemetry_midi_match=bool(integrity["performance_telemetry_midi_match"]),
        observed_end_source=end_source,
        reference_chords=reference_chords,
        legacy_calibration=None if audit.supplied else calibration,
    )

    labels = configured_labels(config)
    hand_mapping = (parser_infer_hands(parse_midi_song_for_hand_inference(reference), "原曲 MIDI")
                    if reference_chords else {"left": None, "right": None})
    features = extract_reference_features(reference_chords, hand_mapping,
                                          kc_labeler_version=kc_labeler_version)
    hands = labels.build_attempt_events_data(features, df)
    # Error codes belong exclusively to target score notes. An insertion is an
    # extra performance note, so it has no target-note reject_reason; it is
    # represented by events_data.extra_notes instead.
    df["reject_reason"] = [
        None if row.alignment_type == "insertion" else 1 if row.alignment_type == "deletion"
        else labels.classify_matched_note(row, "ref_pitch", "performance_pitch", "timing_deviation_global_aligned")
        for _, row in df.iterrows()
    ]
    df["is_correct"] = (df.alignment_type == "match") & (df.reject_reason == 0)
    df["within_observed_range"] = True
    df["timing_labels_valid"] = quality_valid
    score_hash, performance_hash = digest(reference.read_bytes()), digest(performance.read_bytes())
    run_id = alignment_run_id(
        reference, performance, attempt_id, config, performance_events, explicit_end,
        transport_segments, origin, kc_labeler_version=kc_labeler_version,
    )
    df["alignment_event_id"] = [stable_id(run_id, index) for index in range(len(df))]
    for key, value in {
        "alignment_run_id": run_id, "attempt_id": attempt_id,
        "score_version": score_hash, "performance_hash": performance_hash,
        "alignment_version": config.version,
    }.items():
        df[key] = value
    expected = int(df.alignment_type.isin(["match", "deletion"]).sum())
    correct = int(df.is_correct.sum())
    summary = {
        "expected_notes": expected, "correct_notes": correct,
        "extra_notes": int((df.alignment_type == "insertion").sum()),
        "missing_notes": int((df.alignment_type == "deletion").sum()),
        "accuracy": float(correct / expected) if expected and quality_valid else None,
        "timing_labels_valid": quality_valid,
        "alignment_quality_valid": quality_valid,
        "alignment_quality_reason": quality_reason,
    }
    context = {
        "time_basis": time_basis, "reference_origin_sec": origin,
        "observed_end_sec": observed_end, "observed_end_source": end_source,
        "transport_segments_count": len(transport_segments) if isinstance(transport_segments, list) else None,
        "transport_audit_supplied": transport_supplied,
        "event_audit_supplied": audit.supplied,
    }
    diagnostics = {
        "matcher": matcher_version(), "matching_method": matching_method,
        "time_basis": time_basis, "reference_origin_sec": origin,
        "observed_end_sec": observed_end, "observed_end_source": end_source,
        "reference_notes_total": len(reference_all), "reference_notes_observed": len(reference_notes),
        "reference_notes_excluded": len(reference_all) - len(reference_notes),
        "reference_chords_observed": len(reference_chords), "performance_chords": len(performance_chords),
        "performance_event_count": audit.event_count if audit.supplied else None,
        "performance_event_invalid_count": audit.invalid_event_count if audit.supplied else None,
        "non_monotonic_transport": non_monotonic_transport,
        "transport_segment_invalid_count": invalid_transport_segments if transport_supplied else None,
        "raw_timing_median_sec": raw_median, "raw_timing_mad_sec": raw_mad,
        "match_window_sec": config.match_window_sec,
        "wrong_pitch_window_sec": config.wrong_pitch_window_sec,
        **integrity,
        **calibration,
        # These values must come last. ``calibration`` reports its own status
        # (e.g. “not applied” for player-song-time), but it is not the overall
        # semantic quality decision. Letting it overwrite this gate would send
        # a malformed/looped/empty attempt to Gold.
        "alignment_quality_valid": quality_valid,
        "alignment_quality_reason": quality_reason,
    }
    if stage_sink:
        stage_sink(run_id, {
            "attempt_id": attempt_id, "score_version": score_hash,
            "performance_hash": performance_hash, "config": config.identity,
            "matcher_version": matcher_version(), "alignment_context": context,
            "raw_matches": raw_records,
        })
    return {
        "schema_version": 3, "kc_labeler_version": features.kc_labeler_version, "alignment_run_id": run_id, "attempt_id": attempt_id,
        "score_version": score_hash, "performance_hash": performance_hash,
        "config": config.identity, "matcher_version": matcher_version(),
        "alignment_context": context, "diagnostics": diagnostics, "summary": summary,
        "raw_matches": raw_records, "notes": alignment_records(df),
        "events_data": hands,
    }
