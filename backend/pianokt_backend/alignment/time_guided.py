"""Time-guided, chord-aware MIDI alignment for challenge recordings.

Challenge recordings are not free performances: every input event is observed
while a score transport is running.  Treating the two MIDI files as arbitrary
sequences and allowing a global DTW to search the full score loses that useful
fact (and can match a short opening phrase to a repeated phrase much later in
the score).

This module keeps the matcher deliberately small and inspectable:

* reference and performance notes are grouped into near-simultaneous chords;
* chord groups are aligned by a *time-banded*, monotonic edit-distance path;
* exact pitch matches are paired first inside each matched chord;
* unmatched reference/performance notes become deletions/insertions.

The matcher never invents a large global time shift.  A caller may still apply
a separately validated calibration offset for legacy MIDI recordings, but raw
player-song-time timing is always retained in the returned rows.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from math import isfinite
from typing import Any, Iterable

import mido
import numpy as np


@dataclass(frozen=True)
class MidiNote:
    """A note with both musical and source-MIDI identity preserved."""

    onset_sec: float
    duration_sec: float
    pitch: int
    velocity: int
    track: int | None
    channel: int
    onset_tick: int | None
    sequence: int
    # Browser telemetry keeps its original, absolute player-song time here.
    # ``onset_sec`` may be normalised to the reference snapshot origin later.
    player_song_time_sec: float | None = None


@dataclass(frozen=True)
class Chord:
    """A score/performance onset group."""

    onset_sec: float
    notes: tuple[MidiNote, ...]


@dataclass(frozen=True)
class PerformanceEvents:
    """Parsed browser note events and audit health information."""

    notes: tuple[MidiNote, ...]
    supplied: bool
    usable: bool
    non_monotonic_transport: bool
    invalid_event_count: int
    event_count: int


def _tempo_segments(midi: mido.MidiFile) -> list[tuple[int, float, int]]:
    """Build a global MIDI tick -> seconds tempo map.

    MIDI type-1 tracks have independent delta-times but a shared tempo map.
    Reading all tempo messages first avoids accidentally using a track-local
    clock for note events.
    """

    tempo_events: list[tuple[int, int, int]] = [(0, -1, 500_000)]
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        for message in track:
            tick += int(message.time)
            if message.type == "set_tempo":
                tempo_events.append((tick, track_index, int(message.tempo)))

    # A score may contain duplicate tempo declarations at one tick.  The last
    # declaration in deterministic track order wins.
    tempo_events.sort(key=lambda item: (item[0], item[1]))
    compact: list[tuple[int, int]] = []
    for tick, _, tempo in tempo_events:
        if compact and compact[-1][0] == tick:
            compact[-1] = (tick, tempo)
        else:
            compact.append((tick, tempo))

    segments: list[tuple[int, float, int]] = []
    seconds = 0.0
    previous_tick = compact[0][0]
    previous_tempo = compact[0][1]
    segments.append((previous_tick, seconds, previous_tempo))
    for tick, tempo in compact[1:]:
        seconds += mido.tick2second(
            tick - previous_tick,
            midi.ticks_per_beat,
            previous_tempo,
        )
        segments.append((tick, seconds, tempo))
        previous_tick = tick
        previous_tempo = tempo
    return segments


def _tick_to_seconds(
    tick: int,
    ticks_per_beat: int,
    segments: list[tuple[int, float, int]],
) -> float:
    starts = [segment[0] for segment in segments]
    index = int(np.searchsorted(starts, tick, side="right") - 1)
    index = max(0, index)
    start_tick, start_seconds, tempo = segments[index]
    return float(
        start_seconds
        + mido.tick2second(tick - start_tick, ticks_per_beat, tempo)
    )


def read_midi_notes(path: str) -> list[MidiNote]:
    """Read note-on/off pairs while retaining the original reference track.

    ``pretty_midi`` is convenient but drops the source MIDI track identity
    needed by the existing hand-event builder.  This parser intentionally
    keeps that identity and supports tempo changes.
    """

    midi = mido.MidiFile(path)
    segments = _tempo_segments(midi)
    notes: list[MidiNote] = []
    sequence = 0

    for track_index, track in enumerate(midi.tracks):
        tick = 0
        active: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)
        for message in track:
            tick += int(message.time)
            if not hasattr(message, "channel"):
                continue
            if message.type == "note_on" and int(message.velocity) > 0:
                active[(int(message.channel), int(message.note))].append(
                    (tick, int(message.velocity), sequence)
                )
                sequence += 1
                continue
            is_note_off = message.type == "note_off" or (
                message.type == "note_on" and int(message.velocity) == 0
            )
            if not is_note_off:
                continue
            key = (int(message.channel), int(message.note))
            if not active[key]:
                continue
            onset_tick, velocity, note_sequence = active[key].pop(0)
            onset = _tick_to_seconds(onset_tick, midi.ticks_per_beat, segments)
            end = _tick_to_seconds(tick, midi.ticks_per_beat, segments)
            notes.append(
                MidiNote(
                    onset_sec=onset,
                    duration_sec=max(0.0, end - onset),
                    pitch=int(message.note),
                    velocity=velocity,
                    track=track_index,
                    channel=int(message.channel),
                    onset_tick=onset_tick,
                    sequence=note_sequence,
                )
            )

        # An unclosed input key should not disappear from an audit result.
        # It has a zero duration rather than an invented end time.
        for (channel, pitch), pending in active.items():
            for onset_tick, velocity, note_sequence in pending:
                notes.append(
                    MidiNote(
                        onset_sec=_tick_to_seconds(
                            onset_tick, midi.ticks_per_beat, segments
                        ),
                        duration_sec=0.0,
                        pitch=pitch,
                        velocity=velocity,
                        track=track_index,
                        channel=channel,
                        onset_tick=onset_tick,
                        sequence=note_sequence,
                    )
                )

    return sorted(
        notes,
        key=lambda note: (
            note.onset_sec,
            note.track if note.track is not None else -1,
            note.pitch,
            note.sequence,
        ),
    )


def _event_type(value: Any) -> str | None:
    value = str(value or "").strip().lower()
    if value in {"note_on", "on", "down", "press"}:
        return "note_on"
    if value in {"note_off", "off", "up", "release"}:
        return "note_off"
    return None


def _event_time(event: dict[str, Any]) -> float | None:
    value = event.get(
        "player_song_time_sec",
        event.get("player_song_time", event.get("playerSongTime")),
    )
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) and result >= 0 else None


def _normalise_event_collection(
    performance_events: Any,
) -> tuple[list[dict[str, Any]], bool]:
    """Accept the browser audit object as well as a bare event list."""

    if performance_events is None:
        return [], False
    if isinstance(performance_events, dict):
        events = performance_events.get("events", performance_events.get("performance_events"))
    else:
        events = performance_events
    if not isinstance(events, list):
        return [], True
    return [event for event in events if isinstance(event, dict)], True


def performance_notes_from_events(
    performance_events: Any,
    monotonic_tolerance_sec: float = 0.030,
) -> PerformanceEvents:
    """Turn browser event audit data into canonical performance notes.

    Event order is deliberately sequence/order based, never re-sorted by song
    time.  A loop or seek that moves transport backwards is a quality failure;
    sorting it would hide a broken time basis and create false matches.
    """

    events, supplied = _normalise_event_collection(performance_events)
    if not supplied:
        return PerformanceEvents((), False, False, False, 0, 0)

    pending: dict[int, list[dict[str, Any]]] = defaultdict(list)
    notes: list[MidiNote] = []
    invalid = 0
    previous_sequence: int | None = None
    previous_song_time: float | None = None
    previous_performance_now: float | None = None
    non_monotonic = False

    # Never reorder the evidence. A valid browser sidecar has an increasing
    # sequence already; sorting a malformed stream could turn a loop/seek into
    # a plausible-looking performance.
    for wire_index, event in enumerate(events):
        kind = _event_type(event.get("type"))
        event_time = _event_time(event)
        try:
            pitch = int(event.get("pitch", event.get("note")))
            velocity = int(round(float(event.get("velocity", 0))))
        except (TypeError, ValueError):
            invalid += 1
            continue
        sequence = event.get("sequence", wire_index)
        try:
            sequence = int(sequence)
        except (TypeError, ValueError):
            invalid += 1
            sequence = wire_index
        if previous_sequence is not None and sequence <= previous_sequence:
            invalid += 1
        previous_sequence = sequence

        performance_now = event.get(
            "performance_now_timestamp_ms", event.get("performanceNowTimestamp")
        )
        try:
            performance_now = float(performance_now)
        except (TypeError, ValueError):
            performance_now = None

        if (
            kind is None
            or event_time is None
            or not 0 <= pitch <= 127
            or not 0 <= velocity <= 127
            or performance_now is None
            or not isfinite(performance_now)
            or performance_now < 0
        ):
            invalid += 1
            continue

        if (
            previous_song_time is not None
            and event_time < previous_song_time - monotonic_tolerance_sec
        ):
            non_monotonic = True
        if previous_performance_now is not None and performance_now < previous_performance_now:
            non_monotonic = True
        previous_song_time = event_time
        previous_performance_now = performance_now

        # Web MIDI reports note-on velocity 0 as a note-off convention.
        if kind == "note_on" and velocity <= 0:
            kind = "note_off"

        if kind == "note_on":
            pending[pitch].append(
                {
                    "onset_sec": event_time,
                    "velocity": max(0, min(127, velocity)),
                    "sequence": sequence,
                }
            )
            continue

        if pending[pitch]:
            start = pending[pitch].pop(0)
            notes.append(
                MidiNote(
                    onset_sec=float(start["onset_sec"]),
                    duration_sec=max(0.0, event_time - float(start["onset_sec"])),
                    pitch=pitch,
                    velocity=int(start["velocity"]),
                    track=None,
                    channel=0,
                    onset_tick=None,
                    sequence=int(start["sequence"]),
                    player_song_time_sec=float(start["onset_sec"]),
                )
            )

    for pitch, starts in pending.items():
        for start in starts:
            notes.append(
                MidiNote(
                    onset_sec=float(start["onset_sec"]),
                    duration_sec=0.0,
                    pitch=pitch,
                    velocity=int(start["velocity"]),
                    track=None,
                    channel=0,
                    onset_tick=None,
                    sequence=int(start["sequence"]),
                    player_song_time_sec=float(start["onset_sec"]),
                )
            )

    # Do not sort on ``onset_sec`` here.  The matcher checks non-monotonic
    # transport first, and only receives a time-sorted input if it is safe.
    notes.sort(key=lambda note: note.sequence)
    return PerformanceEvents(
        notes=tuple(notes),
        supplied=True,
        usable=bool(notes),
        non_monotonic_transport=non_monotonic,
        invalid_event_count=invalid,
        event_count=len(events),
    )


def validate_transport_segments(
    transport_segments: Any,
    monotonic_tolerance_sec: float = 0.030,
    require_complete_cycle: bool = False,
) -> tuple[bool, int, bool]:
    """Validate the transport audit without turning pauses into musical time.

    Returns ``(supplied, invalid_count, non_monotonic)``. A pause/resume pair
    is valid even though the song time is unchanged; a backwards song clock is
    not silently repaired because it represents a loop or seek.
    """

    if transport_segments is None:
        return False, 1 if require_complete_cycle else 0, False
    if not isinstance(transport_segments, list):
        return True, 1, False

    invalid = 0
    if require_complete_cycle and not transport_segments:
        invalid += 1
    non_monotonic = False
    previous_sequence: int | None = None
    previous_song_time: float | None = None
    previous_now: float | None = None
    state = "before_start"
    for wire_index, segment in enumerate(transport_segments):
        if not isinstance(segment, dict):
            invalid += 1
            continue
        sequence = segment.get("sequence", wire_index)
        event_type = str(segment.get("type", "")).strip().lower()
        song_time = _event_time(segment)
        now = segment.get(
            "performance_now_timestamp_ms", segment.get("performanceNowTimestamp")
        )
        try:
            sequence = int(sequence)
            now = float(now)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if (
            sequence < 0
            or (previous_sequence is not None and sequence <= previous_sequence)
            or event_type not in {"start", "resume", "pause", "stop"}
            or song_time is None
            or now < 0
            or not isfinite(now)
        ):
            invalid += 1
            continue
        if previous_song_time is not None and song_time < previous_song_time - monotonic_tolerance_sec:
            non_monotonic = True
        if previous_now is not None and now < previous_now:
            non_monotonic = True
        previous_sequence = sequence
        previous_song_time = song_time
        previous_now = now

        if event_type == "start":
            if state != "before_start":
                invalid += 1
            state = "active"
        elif event_type == "pause":
            if state != "active":
                invalid += 1
            state = "paused"
        elif event_type == "resume":
            if state != "paused":
                invalid += 1
            state = "active"
        elif event_type == "stop":
            if state not in {"active", "paused"}:
                invalid += 1
            state = "stopped"
    if require_complete_cycle and state != "stopped":
        invalid += 1
    return True, invalid, non_monotonic


def normalise_player_song_time(
    notes: Iterable[MidiNote], reference_origin_sec: float,
) -> list[MidiNote]:
    """Express browser player-song-time notes in snapshot-relative seconds.

    The browser transport is absolute in the loaded song while a reference
    snapshot starts at zero for a selected range.  Keep the original absolute
    timestamp in ``player_song_time_sec`` and only shift the matcher-facing
    onset.
    """

    return [
        replace(note, onset_sec=note.onset_sec - reference_origin_sec)
        for note in notes
    ]


def group_chords(notes: Iterable[MidiNote], tolerance_sec: float) -> list[Chord]:
    """Group notes whose onsets belong to the same chord attack."""

    ordered = sorted(notes, key=lambda note: (note.onset_sec, note.sequence, note.pitch))
    if not ordered:
        return []
    groups: list[list[MidiNote]] = [[ordered[0]]]
    group_start = ordered[0].onset_sec
    for note in ordered[1:]:
        if note.onset_sec - group_start <= tolerance_sec:
            groups[-1].append(note)
            continue
        groups.append([note])
        group_start = note.onset_sec
    return [
        Chord(
            onset_sec=float(np.median([note.onset_sec for note in group])),
            notes=tuple(group),
        )
        for group in groups
    ]


def _pitch_overlap(left: Chord, right: Chord) -> int:
    return sum((Counter(note.pitch for note in left.notes) & Counter(note.pitch for note in right.notes)).values())


def _chord_match_cost(
    reference: Chord,
    performance: Chord,
    *,
    match_window_sec: float,
    wrong_pitch_window_sec: float,
) -> float:
    """Cost for one chord-to-chord match, or infinity if not locally plausible."""

    distance = abs(reference.onset_sec - performance.onset_sec)
    if distance > match_window_sec:
        return float("inf")
    overlap = _pitch_overlap(reference, performance)
    # A zero-overlap group is only a wrong-pitch candidate when it happens at
    # essentially the same musical instant.  Otherwise delete+insert is more
    # truthful than inventing a substitution across beats.
    if overlap == 0 and distance > wrong_pitch_window_sec:
        return float("inf")
    largest = max(len(reference.notes), len(performance.notes), 1)
    pitch_penalty = 1.0 - overlap / largest
    time_penalty = 0.35 * distance / max(match_window_sec, 1e-9)
    # A small base cost makes two independent groups preferable to a wildly
    # different same-time chord only when that is actually cheaper.
    return float(0.15 + 0.90 * pitch_penalty + time_penalty)


def _align_chords_dynamic(
    reference: list[Chord],
    performance: list[Chord],
    *,
    match_window_sec: float,
    wrong_pitch_window_sec: float,
) -> list[tuple[str, int | None, int | None]]:
    """Monotonic edit-distance alignment of chord groups."""

    n, m = len(reference), len(performance)
    score = np.full((n + 1, m + 1), np.inf, dtype=float)
    back = np.zeros((n + 1, m + 1), dtype=np.int8)
    score[0, 0] = 0.0
    score[1:, 0] = np.arange(1, n + 1, dtype=float)
    score[0, 1:] = np.arange(1, m + 1, dtype=float)
    # 1 = deletion, 2 = insertion, 3 = match
    back[1:, 0] = 1
    back[0, 1:] = 2

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            delete = score[i - 1, j] + 1.0
            insert = score[i, j - 1] + 1.0
            match_cost = _chord_match_cost(
                reference[i - 1],
                performance[j - 1],
                match_window_sec=match_window_sec,
                wrong_pitch_window_sec=wrong_pitch_window_sec,
            )
            match = score[i - 1, j - 1] + match_cost
            if match <= delete and match <= insert:
                score[i, j] = match
                back[i, j] = 3
            elif delete <= insert:
                score[i, j] = delete
                back[i, j] = 1
            else:
                score[i, j] = insert
                back[i, j] = 2

    operations: list[tuple[str, int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        operation = int(back[i, j])
        if operation == 3:
            operations.append(("match", i - 1, j - 1))
            i -= 1
            j -= 1
        elif operation == 1:
            operations.append(("deletion", i - 1, None))
            i -= 1
        else:
            operations.append(("insertion", None, j - 1))
            j -= 1
    operations.reverse()
    return operations


def _align_chords_greedy(
    reference: list[Chord],
    performance: list[Chord],
    *,
    match_window_sec: float,
    wrong_pitch_window_sec: float,
) -> list[tuple[str, int | None, int | None]]:
    """Bounded-memory monotonic fallback for unusually large scores."""

    operations: list[tuple[str, int | None, int | None]] = []
    i = j = 0
    while i < len(reference) and j < len(performance):
        ref = reference[i]
        perf = performance[j]
        if perf.onset_sec < ref.onset_sec - match_window_sec:
            operations.append(("insertion", None, j))
            j += 1
            continue
        if ref.onset_sec < perf.onset_sec - match_window_sec:
            operations.append(("deletion", i, None))
            i += 1
            continue
        if np.isfinite(
            _chord_match_cost(
                ref,
                perf,
                match_window_sec=match_window_sec,
                wrong_pitch_window_sec=wrong_pitch_window_sec,
            )
        ):
            operations.append(("match", i, j))
            i += 1
            j += 1
        elif perf.onset_sec <= ref.onset_sec:
            operations.append(("insertion", None, j))
            j += 1
        else:
            operations.append(("deletion", i, None))
            i += 1
    operations.extend(("deletion", index, None) for index in range(i, len(reference)))
    operations.extend(("insertion", None, index) for index in range(j, len(performance)))
    return operations


def align_chords(
    reference: list[Chord],
    performance: list[Chord],
    *,
    match_window_sec: float,
    wrong_pitch_window_sec: float,
    max_alignment_cells: int,
) -> tuple[list[tuple[str, int | None, int | None]], str]:
    """Choose a dynamic or bounded-memory monotonic chord alignment."""

    cells = (len(reference) + 1) * (len(performance) + 1)
    if cells <= max_alignment_cells:
        return (
            _align_chords_dynamic(
                reference,
                performance,
                match_window_sec=match_window_sec,
                wrong_pitch_window_sec=wrong_pitch_window_sec,
            ),
            "time_banded_chord_dynamic_programming",
        )
    return (
        _align_chords_greedy(
            reference,
            performance,
            match_window_sec=match_window_sec,
            wrong_pitch_window_sec=wrong_pitch_window_sec,
        ),
        "time_banded_chord_greedy_fallback",
    )


def reference_note_id(note: MidiNote) -> str:
    return f"r{note.track if note.track is not None else 'x'}_n{note.sequence}"


def _row(
    alignment_type: str,
    reference: MidiNote | None,
    performance: MidiNote | None,
    reference_chord_index: int | None,
    performance_chord_index: int | None,
) -> dict[str, Any]:
    """Build the compatibility row consumed by prototype.py and the lake."""

    return {
        "alignment_type": alignment_type,
        # These source identities survive materialization and let Bronze/Silver
        # distinguish repeated pitches at different score positions.  They are
        # deterministic within the immutable reference/performance artifacts.
        "ref_id": (
            reference_note_id(reference)
            if reference is not None
            else None
        ),
        "performance_id": (
            f"p_n{performance.sequence}" if performance is not None else None
        ),
        "ref_pitch": reference.pitch if reference is not None else None,
        "performance_pitch": performance.pitch if performance is not None else None,
        "ref_onset_sec": reference.onset_sec if reference is not None else None,
        "performance_onset_sec": performance.onset_sec if performance is not None else None,
        # The matcher-facing onset above is reference-window relative.  Preserve
        # the original absolute player transport clock separately for audit.
        "performance_player_song_time_sec": (
            performance.player_song_time_sec if performance is not None else None
        ),
        "ref_duration_sec": reference.duration_sec if reference is not None else None,
        "performance_duration_sec": performance.duration_sec if performance is not None else None,
        "ref_track": reference.track if reference is not None else None,
        "ref_onset_tick": reference.onset_tick if reference is not None else None,
        "ref_channel": reference.channel if reference is not None else None,
        "ref_velocity": reference.velocity if reference is not None else None,
        "performance_track": performance.track if performance is not None else None,
        "performance_onset_tick": performance.onset_tick if performance is not None else None,
        "performance_channel": performance.channel if performance is not None else None,
        "performance_velocity": performance.velocity if performance is not None else None,
        "reference_chord_index": reference_chord_index,
        "performance_chord_index": performance_chord_index,
    }


def _pair_chord_notes(
    reference: Chord,
    performance: Chord,
    reference_chord_index: int,
    performance_chord_index: int,
) -> list[dict[str, Any]]:
    """Pair exact pitches first, then local substitutions, then leftovers."""

    ref_by_pitch: dict[int, list[MidiNote]] = defaultdict(list)
    perf_by_pitch: dict[int, list[MidiNote]] = defaultdict(list)
    for note in reference.notes:
        ref_by_pitch[note.pitch].append(note)
    for note in performance.notes:
        perf_by_pitch[note.pitch].append(note)
    for notes in ref_by_pitch.values():
        notes.sort(key=lambda note: (note.sequence, note.onset_sec))
    for notes in perf_by_pitch.values():
        notes.sort(key=lambda note: (note.sequence, note.onset_sec))

    rows: list[dict[str, Any]] = []
    remaining_reference: list[MidiNote] = []
    remaining_performance: list[MidiNote] = []
    for pitch in sorted(set(ref_by_pitch) | set(perf_by_pitch)):
        ref_notes = ref_by_pitch[pitch]
        perf_notes = perf_by_pitch[pitch]
        paired = min(len(ref_notes), len(perf_notes))
        for index in range(paired):
            rows.append(
                _row(
                    "match",
                    ref_notes[index],
                    perf_notes[index],
                    reference_chord_index,
                    performance_chord_index,
                )
            )
        remaining_reference.extend(ref_notes[paired:])
        remaining_performance.extend(perf_notes[paired:])

    # Pair the remaining notes by closest pitch.  They are known to be in the
    # same local, monotonic chord pair, so this is a meaningful substitution,
    # not a cross-bar DTW match.
    while remaining_reference and remaining_performance:
        ref_index, perf_index = min(
            (
                (left, right)
                for left in range(len(remaining_reference))
                for right in range(len(remaining_performance))
            ),
            key=lambda pair: (
                abs(
                    remaining_reference[pair[0]].pitch
                    - remaining_performance[pair[1]].pitch
                ),
                remaining_reference[pair[0]].sequence,
                remaining_performance[pair[1]].sequence,
            ),
        )
        rows.append(
            _row(
                "match",
                remaining_reference.pop(ref_index),
                remaining_performance.pop(perf_index),
                reference_chord_index,
                performance_chord_index,
            )
        )
    rows.extend(
        _row("deletion", note, None, reference_chord_index, None)
        for note in remaining_reference
    )
    rows.extend(
        _row("insertion", None, note, None, performance_chord_index)
        for note in remaining_performance
    )
    return rows


def rows_from_operations(
    reference: list[Chord],
    performance: list[Chord],
    operations: Iterable[tuple[str, int | None, int | None]],
) -> list[dict[str, Any]]:
    """Expand chord operations into one row per alignment event."""

    rows: list[dict[str, Any]] = []
    for operation, reference_index, performance_index in operations:
        if operation == "match":
            assert reference_index is not None and performance_index is not None
            rows.extend(
                _pair_chord_notes(
                    reference[reference_index],
                    performance[performance_index],
                    reference_index,
                    performance_index,
                )
            )
        elif operation == "deletion":
            assert reference_index is not None
            rows.extend(
                _row("deletion", note, None, reference_index, None)
                for note in reference[reference_index].notes
            )
        else:
            assert performance_index is not None
            rows.extend(
                _row("insertion", None, note, None, performance_index)
                for note in performance[performance_index].notes
            )
    return rows
