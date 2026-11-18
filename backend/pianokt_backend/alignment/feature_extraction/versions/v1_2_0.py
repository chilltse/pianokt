"""Frozen KC 1.2.0 algorithm; add a new module for semantic changes."""
from functools import lru_cache
from math import isfinite
from ...time_guided import reference_note_id
from ..types import ReferenceFeatures, ReferenceGroupingMismatch

KC_LABELER_VERSION = "1.2.0"

KEY_X = (0, .5, 1, 1.5, 2, 3, 3.5, 4, 4.5, 5, 5.5, 6)


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


def extract_reference_features(reference_chords, hand_mapping):
    groups = []
    events = []
    seen = set()
    previous_onset = None
    for index, chord in enumerate(reference_chords):
        if not isfinite(chord.onset_sec) or (previous_onset is not None and chord.onset_sec <= previous_onset):
            raise ReferenceGroupingMismatch(f"Invalid canonical onset group={index}")
        previous_onset = chord.onset_sec
        members = {}
        for note in chord.notes:
            identity = reference_note_id(note)
            if identity in seen:
                raise ReferenceGroupingMismatch(f"duplicate ref_id={identity} group={index}")
            seen.add(identity)
            members[identity] = note
        groups.append(members)
        targets = []
        for hand in ("left", "right"):
            by_pitch = {}
            track = hand_mapping.get(hand)
            for identity, note in members.items():
                if track is not None and note.track == track:
                    by_pitch.setdefault(note.pitch, []).append(identity)
            targets.append(dict(sorted(by_pitch.items())))
        if any(targets):
            events.append(dict(group=index, onset=chord.onset_sec,
                               pitches=[list(hand) for hand in targets], targets=targets))
    previous_hand: dict[str, set[int]] = {"left": set(), "right": set()}
    previous_hand_time: dict[str, float | None] = {"left": None, "right": None}
    previous_global: float | None = None
    onset_seconds=[]; intervals=[]; vectors=[]
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
        onset_seconds.append(onset); intervals.append([*hand_intervals, global_interval])
        vectors.append([any(p % 12 in (1, 3, 6, 8, 10) for p in flat), len(current["left"]) + len(current["right"]), bool(current["left"] and current["right"]), _interval_bin(global_interval), changes, _move_bin(mean_move)])
    for i, event in enumerate(events):
        event["intervals"] = intervals[i]
        event["vector"] = vectors[i]
    if any(a >= b for a, b in zip(onset_seconds, onset_seconds[1:])):
        raise ReferenceGroupingMismatch("Reference onsets must be strictly increasing")
    return ReferenceFeatures(tuple(groups), tuple(events), KC_LABELER_VERSION, tuple(KC_FEATURE_NAMES))
