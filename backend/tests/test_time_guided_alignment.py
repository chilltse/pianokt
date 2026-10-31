"""Regression tests for time-guided Challenge alignment semantics."""

from __future__ import annotations

import copy

import mido

from pianokt_backend.alignment.service import align
from pianokt_backend.contracts import AlignmentConfig
from pianokt_backend.pipeline.jobs import _result_quality, publish_alignment
from pianokt_backend.pipeline.tables import Tables
from pianokt_backend.worker import _alignment_window


TICKS_PER_SECOND = 960  # 120 BPM, 480 ticks-per-beat


def write_midi(path, notes):
    """Write ``(pitch, onset_sec, duration_sec)`` notes in input chord order."""

    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    events = []
    for order, (pitch, onset, duration) in enumerate(notes):
        events.append((round(onset * TICKS_PER_SECOND), 1, order, "on", pitch, 90))
        events.append(
            (round((onset + duration) * TICKS_PER_SECOND), 0, order, "off", pitch, 0)
        )
    events.sort(key=lambda event: event[:3])
    previous_tick = 0
    for tick, _, _, kind, pitch, velocity in events:
        track.append(
            mido.Message(
                "note_on" if kind == "on" else "note_off",
                note=pitch,
                velocity=velocity,
                time=tick - previous_tick,
            )
        )
        previous_tick = tick
    midi.save(path)


def sidecar(notes, *, origin=0.0, include_stop=True):
    """Make browser evidence for snapshot-relative performance notes."""

    raw_events = []
    for order, (pitch, onset, duration) in enumerate(notes):
        raw_events.extend(
            [
                (onset, 0, order, "note_on", pitch, 90),
                (onset + duration, 1, order, "note_off", pitch, 0),
            ]
        )
    raw_events.sort(key=lambda event: event[:3])
    events = []
    for sequence, (song_time, _, _, event_type, pitch, velocity) in enumerate(raw_events):
        now = 1_000.0 + song_time * 1_000
        events.append(
            {
                "sequence": sequence,
                "type": event_type,
                "midiEventTimestamp": now,
                "playerSongTime": origin + song_time,
                "performanceNowTimestamp": now,
                "pitch": pitch,
                "velocity": velocity,
                "timestampSource": "web_midi",
            }
        )
    end = origin + max((onset + duration for _, onset, duration in notes), default=0.0)
    segments = [
        {
            "sequence": 0,
            "type": "start",
            "playerSongTime": origin,
            "performanceNowTimestamp": 0.0,
        }
    ]
    if include_stop:
        segments.append(
            {
                "sequence": 1,
                "type": "stop",
                "playerSongTime": end,
                "performanceNowTimestamp": max(1.0, (end - origin) * 1_000 + 1_000),
            }
        )
    return {"schemaVersion": 1, "events": events, "transportSegments": segments}


def enrich_for_lake(result, audit):
    result.update(
        learner_key="learner",
        song_id="song",
        session_id="session",
        practice_metadata={"session_id": "session", "settings": {}},
        attempt_created_at="2026-09-01T00:00:00+00:00",
        alignment_completed_at="2026-09-01T00:01:00+00:00",
        performance_events=audit["events"],
        transport_segments=audit["transportSegments"],
    )
    return result


def test_short_opening_cannot_match_repeated_late_phrase(tmp_path):
    reference = tmp_path / "reference.mid"
    performance = tmp_path / "performance.mid"
    opening = [(60, 0.0, 0.1), (62, 0.5, 0.1), (64, 1.0, 0.1)]
    write_midi(reference, opening + [(60, 35.0, 0.1), (62, 35.5, 0.1), (64, 36.0, 0.1)])
    played = [(60, 0.1, 0.1), (62, 0.6, 0.1), (64, 1.1, 0.1)]
    write_midi(performance, played)
    audit = sidecar(played)

    result = align(
        reference,
        performance,
        "opening",
        performance_events=audit,
        observed_end_sec=1.2,
        transport_segments=audit["transportSegments"],
    )

    assert result["summary"]["alignment_quality_valid"] is True
    assert result["diagnostics"]["reference_notes_observed"] == 3
    assert result["diagnostics"]["reference_notes_excluded"] == 3
    assert result["diagnostics"]["global_offset_sec"] == 0.0
    assert round(result["diagnostics"]["raw_timing_median_sec"], 4) == 0.1
    assert {note["ref_onset_sec"] for note in result["notes"]} == {0.0, 0.5, 1.0}
    assert {round(note["raw_timing_deviation_sec"], 4) for note in result["notes"]} == {0.1}
    # Only the performance-covered score range becomes target events. The late
    # repeated phrase is absent, not labelled as three missed notes.
    assert result["events_data"]["onset_seconds"] == [0.0, 0.5, 1.0]
    assert all(label == 0 for label in result["events_data"]["reject_reason"]["event"])


def test_seeked_snapshot_origin_and_reversed_chord_order_are_valid(tmp_path):
    reference = tmp_path / "reference.mid"
    performance = tmp_path / "performance.mid"
    # The snapshot begins at absolute song time 10. The physical player presses
    # E before C, while MIDI parsing normalizes the same-tick chord order.
    expected = [(60, 0.0, 0.2), (64, 0.0, 0.2), (67, 0.5, 0.2)]
    played = [(64, 0.1, 0.2), (60, 0.1, 0.2), (67, 0.6, 0.2)]
    write_midi(reference, expected)
    write_midi(performance, played)
    audit = sidecar(played, origin=10.0)

    result = align(
        reference,
        performance,
        "seeked",
        performance_events=audit,
        observed_end_sec=0.9,
        transport_segments=audit["transportSegments"],
        reference_origin_sec=10.0,
    )

    assert result["summary"]["alignment_quality_valid"] is True
    assert result["diagnostics"]["performance_telemetry_midi_match"] is True
    assert result["diagnostics"]["reference_origin_sec"] == 10.0
    matched = [note for note in result["notes"] if note["alignment_type"] == "match"]
    assert len(matched) == 3
    assert {round(note["performance_player_song_time_sec"], 3) for note in matched} == {10.1, 10.6}
    assert {round(note["raw_timing_deviation_sec"], 3) for note in matched} == {0.1}


def test_broken_transport_or_empty_reference_is_quarantined(tmp_path):
    reference = tmp_path / "reference.mid"
    performance = tmp_path / "performance.mid"
    notes = [(60, 0.1, 0.1)]
    write_midi(reference, [(60, 0.0, 0.1)])
    write_midi(performance, notes)
    audit = sidecar(notes, include_stop=False)
    broken = align(
        reference,
        performance,
        "broken-cycle",
        performance_events=audit,
        observed_end_sec=0.3,
        transport_segments=audit["transportSegments"],
    )
    assert broken["summary"]["alignment_quality_valid"] is False
    assert "invalid_transport_segment_audit" in broken["summary"]["alignment_quality_reason"]

    empty_reference = tmp_path / "empty-reference.mid"
    write_midi(empty_reference, [])
    empty = align(
        empty_reference,
        performance,
        "empty-reference",
        performance_events=sidecar(notes),
        observed_end_sec=0.3,
        transport_segments=sidecar(notes)["transportSegments"],
    )
    assert empty["summary"]["alignment_quality_valid"] is False
    assert "empty_observed_reference" in empty["summary"]["alignment_quality_reason"]
    assert [note["alignment_type"] for note in empty["notes"]] == ["insertion"]


def test_contradictory_snapshot_window_is_never_clamped_to_zero():
    import pytest

    with pytest.raises(ValueError, match="precedes"):
        _alignment_window(
            {
                "observed_range": {"start": 10.0, "end": 5.0},
                "reference_time_origin_sec": 10.0,
            }
        )


def test_gold_is_quality_gated_and_attempt_fact_is_current_per_attempt(tmp_path):
    reference = tmp_path / "reference.mid"
    performance = tmp_path / "performance.mid"
    expected = [(60, 0.0, 0.1), (62, 0.5, 0.1)]
    played = [(60, 0.1, 0.1), (62, 0.6, 0.1)]
    write_midi(reference, expected)
    write_midi(performance, played)
    audit = sidecar(played)

    valid = enrich_for_lake(
        align(
            reference,
            performance,
            "same-attempt",
            performance_events=audit,
            observed_end_sec=0.8,
            transport_segments=audit["transportSegments"],
        ),
        audit,
    )
    tables = Tables(str(tmp_path / "lake"))
    publish_alignment(valid, tables, "alignment/one/result.json")
    assert len(tables.rows("bronze/midi_events")) == len(audit["events"])
    assert len(tables.rows("silver/midi_alignment_events")) == 2
    gold_rows = tables.rows("gold/fact_midi_alignment")
    assert len(gold_rows) == 2
    assert "payload_json" not in gold_rows[0]
    assert gold_rows[0]["performance_player_song_time_sec"] is not None
    assert gold_rows[0]["attempt_created_at_jst"] == "2026-09-01T09:00:00+09:00"
    assert gold_rows[0]["alignment_completed_date_jst"] == "2026-09-01"
    first_attempt = tables.rows("gold/fact_alignment_attempt")[0]
    assert first_attempt["attempt_date_jst"] == "2026-09-01"
    assert first_attempt["alignment_completed_at_jst"] == "2026-09-01T09:01:00+09:00"

    rerun = enrich_for_lake(
        align(
            reference,
            performance,
            "same-attempt",
            config=AlignmentConfig(match_window_sec=0.9),
            performance_events=audit,
            observed_end_sec=0.8,
            transport_segments=audit["transportSegments"],
        ),
        audit,
    )
    publish_alignment(rerun, tables, "alignment/two/result.json")
    attempt_rows = tables.rows("gold/fact_alignment_attempt")
    assert len(attempt_rows) == 1
    assert attempt_rows[0]["attempt_id"] == "same-attempt"
    assert attempt_rows[0]["alignment_run_id"] == rerun["alignment_run_id"]

    invalid = copy.deepcopy(valid)
    invalid["alignment_run_id"] = "invalid-run"
    invalid["alignment_quality_valid"] = False
    invalid["alignment_quality_reason"] = "test_rejection"
    invalid["diagnostics"]["alignment_quality_valid"] = False
    invalid["diagnostics"]["alignment_quality_reason"] = "test_rejection"
    invalid["summary"]["alignment_quality_valid"] = False
    invalid["summary"]["alignment_quality_reason"] = "test_rejection"
    # The event IDs belong to the immutable original result; regenerate a
    # distinct input by changing the attempt so Bronze/Silver can materialize.
    invalid["attempt_id"] = "invalid-attempt"
    invalid["alignment_run_id"] = "invalid-run"
    for index, note in enumerate(invalid["notes"]):
        note["alignment_event_id"] = f"invalid-event-{index}"
        note["alignment_run_id"] = "invalid-run"
        note["attempt_id"] = "invalid-attempt"
    publish_alignment(invalid, tables, "alignment/invalid/result.json")
    assert len(tables.rows("gold/fact_alignment_attempt")) == 1
    assert len(tables.rows("quality/alignment_runs")) == 1
    assert _result_quality({"alignment_run_id": "legacy", "attempt_id": "legacy"}) == (
        False,
        "legacy_alignment_without_quality_gate",
    )
