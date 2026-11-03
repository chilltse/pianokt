"""Canonical v1.1 target-onset labels and KC features; explicit per-call config."""
from pathlib import Path
from typing import Any
from functools import lru_cache
import pandas as pd
import numpy as np
from .hands import parse_midi_song_for_hand_inference, parser_infer_hands
from .labels import classify_matched_note
KEY_X = (0, .5, 1, 1.5, 2, 3, 3.5, 4, 4.5, 5, 5.5, 6)

def sec_to_ms(value):
    return int(round(float(value)*1000)) if pd.notna(value) else None

KC_FEATURE_NAMES = [
    "has_black_key", "simultaneous_note_count", "both_hands",
    "onset_interval_bin_cs", "change_count", "mean_move_bin",
]


def _white_key_x(pitch: int) -> float:
    """Physical white-key coordinate; black keys lie between adjacent whites."""
    octave, pitch_class = divmod(int(pitch), 12)
    return 7 * octave + KEY_X[pitch_class]


def _hand_move(previous: set[int], current: set[int]) -> tuple[float, int]:
    """Maximum-cardinality, minimum white-key-distance matching for one hand."""
    old, new = sorted(previous), sorted(current)
    if not old or not new:
        return 0.0, 0
    # Matching sorted pitches in order is optimal for absolute one-dimensional
    # distance. DP also makes the cardinality rule explicit for unequal chords.
    small, large = (old, new) if len(old) <= len(new) else (new, old)
    @lru_cache(None)
    def solve(i: int, j: int) -> float:
        if i == len(small):
            return 0.0
        if len(large) - j < len(small) - i:
            return float("inf")
        return min(
            abs(_white_key_x(small[i]) - _white_key_x(large[j])) + solve(i + 1, j + 1),
            solve(i, j + 1),
        )
    return solve(0, 0), len(small)


def _interval_bin(seconds: float | None) -> int | None:
    if seconds is None:
        return None
    return 10 if seconds < .2 else 30 if seconds < .4 else 50 if seconds < .6 else 70 if seconds < .8 else 90


def _move_bin(distance: float | None) -> int | None:
    if distance is None:
        return None
    if distance == 0:
        return 0
    if distance <= 2:
        return 1
    if distance <= 5:
        return 2
    if distance <= 9:
        return 3
    return 4


def build_attempt_events_data(
    df: pd.DataFrame,
    reference_midi_path: str | Path,
    timing_threshold_ms: float = 150,
    insertion_attach_window_sec: float = .25,
) -> dict[str, Any]:
    """Build the schema-v1.1 target-onset record from an observed score range.

    ``df`` is already restricted by the time-guided matcher to the score range
    actually covered by the performance. Therefore no score event after the
    played range can become a deletion. The reference MIDI supplies hand truth;
    the dataframe supplies only performance outcomes.
    """
    required = {"alignment_type", "ref_onset_sec", "ref_pitch", "ref_track", "performance_pitch", "performance_onset_sec_global_aligned", "timing_deviation_global_aligned"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Attempt exporter missing columns: {sorted(missing)}")
    song = parse_midi_song_for_hand_inference(reference_midi_path)
    hands = parser_infer_hands(song, "原曲 MIDI")
    track_hand = {track: hand for hand, track in hands.items() if track is not None}
    refs = df[df["alignment_type"].isin(["match", "deletion"])].copy()
    refs = refs[refs["ref_track"].isin(track_hand)].copy()
    refs["_time"] = pd.to_numeric(refs["ref_onset_sec"], errors="coerce").round(9)
    refs = refs[refs["_time"].notna()]
    event_times = sorted(float(value) for value in refs["_time"].unique())
    events: list[dict[str, Any]] = []
    for onset in event_times:
        rows = refs[refs["_time"] == onset]
        per_hand: list[list[int]] = []
        note_reasons: list[list[int]] = []
        timing_offsets: list[list[int | None]] = []
        pitch_offsets: list[list[int | None]] = []
        for hand in ("left", "right"):
            track = hands[hand]
            hand_rows = rows[rows["ref_track"] == track].sort_values("ref_pitch") if track is not None else rows.iloc[0:0]
            # Schema says simultaneous duplicate pitches are de-duplicated.
            chosen = hand_rows.drop_duplicates(subset=["ref_pitch"], keep="first")
            pitches, reasons, timings, pitch_deltas = [], [], [], []
            for _, row in chosen.iterrows():
                pitch = int(round(row["ref_pitch"]))
                pitches.append(pitch)
                if row["alignment_type"] == "deletion":
                    reasons.append(1); timings.append(None); pitch_deltas.append(None)
                    continue
                reasons.append(classify_matched_note(row, "ref_pitch", "performance_pitch", "timing_deviation_global_aligned", timing_threshold_ms))
                timings.append(sec_to_ms(row.get("timing_deviation_global_aligned", timing_threshold_ms)))
                pitch_deltas.append(int(round(row["performance_pitch"] - row["ref_pitch"])) if pd.notna(row.get("performance_pitch")) else None)
            per_hand.append(pitches); note_reasons.append(reasons); timing_offsets.append(timings); pitch_offsets.append(pitch_deltas)
        events.append(dict(onset=onset, pitches=per_hand, note=note_reasons, timing=timing_offsets, pitch_offset=pitch_offsets, extra=[]))

    # Every unmatched performance note is assigned once to the nearest target
    # onset, within the fixed attachment window. It never receives a target-note
    # error code and is never allowed to duplicate an already matched note.
    for _, row in df[df["alignment_type"] == "insertion"].iterrows():
        perf_onset = row.get("performance_onset_sec_global_aligned")
        if not event_times or pd.isna(perf_onset) or pd.isna(row.get("performance_pitch")):
            continue
        index = min(range(len(events)), key=lambda i: abs(events[i]["onset"] - float(perf_onset)))
        if abs(events[index]["onset"] - float(perf_onset)) <= insertion_attach_window_sec:
            events[index]["extra"].append(int(round(row["performance_pitch"])))

    previous_hand: dict[str, set[int]] = {"left": set(), "right": set()}
    previous_hand_time: dict[str, float | None] = {"left": None, "right": None}
    previous_global: float | None = None
    onset_seconds=[]; pitches=[]; intervals=[]; vectors=[]; event_labels=[]; note_labels=[]; timing=[]; pitch_delta=[]; extras=[]
    for event in events:
        onset = event["onset"]; current = {"left": set(event["pitches"][0]), "right": set(event["pitches"][1])}
        hand_intervals=[]; total_distance=0.0; total_matches=0; changes=0
        for hand in ("left", "right"):
            if current[hand]:
                hand_intervals.append(None if previous_hand_time[hand] is None else onset - previous_hand_time[hand])
                distance, matches = _hand_move(previous_hand[hand], current[hand])
                total_distance += distance; total_matches += matches; changes += len(current[hand] - previous_hand[hand])
                previous_hand[hand] = current[hand]; previous_hand_time[hand] = onset
            else:
                hand_intervals.append(None)
        global_interval = None if previous_global is None else onset - previous_global
        previous_global = onset
        flat = current["left"] | current["right"]
        mean_move = total_distance / total_matches if total_matches else None
        onset_seconds.append(onset); pitches.append(event["pitches"]); intervals.append([*hand_intervals, global_interval])
        vectors.append([any(p % 12 in (1, 3, 6, 8, 10) for p in flat), len(current["left"]) + len(current["right"]), bool(current["left"] and current["right"]), _interval_bin(global_interval), changes, _move_bin(mean_move)])
        note_labels.append(event["note"]); timing.append(event["timing"]); pitch_delta.append(event["pitch_offset"]); extras.append(sorted(event["extra"]))
        event_labels.append(int(any(code != 0 for hand in event["note"] for code in hand) or bool(event["extra"])))
    return {
        "kc_feature_names": KC_FEATURE_NAMES, "onset_seconds": onset_seconds, "pitches": pitches,
        "intervals_seconds": intervals, "kc_vectors": vectors,
        "reject_reason": {"event": event_labels, "note": note_labels},
        "timing_offset_ms": timing, "pitch_offset_semitones": pitch_delta, "extra_notes": extras,
    }
