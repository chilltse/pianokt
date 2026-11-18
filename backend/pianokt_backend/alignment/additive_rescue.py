"""Recover unmatched same-pitch notes without changing existing matches."""

from bisect import bisect_left, bisect_right
from collections import defaultdict


def rescue_rows(rows, window_sec):
    # Include the configured boundary despite MIDI timestamp roundoff.
    window_sec += 1e-9
    anchors = defaultdict(list)
    insertions = defaultdict(list)
    for index, row in enumerate(rows):
        if row["alignment_type"] == "insertion":
            insertions[row["performance_pitch"]].append((row["performance_onset_sec"], index))
        elif row["alignment_type"] == "match" and row["ref_pitch"] == row["performance_pitch"]:
            anchors[row["reference_chord_index"]].append(
                (row["ref_onset_sec"], row["performance_onset_sec"])
            )

    for notes in insertions.values():
        notes.sort()

    candidates = []
    for index, row in enumerate(rows):
        if row["alignment_type"] != "deletion":
            continue
        onset = row["ref_onset_sec"]
        nearby_anchors = [
            played for expected, played in anchors[row["reference_chord_index"]]
            if abs(expected - onset) <= 0.03 + 1e-9
        ]
        if not nearby_anchors:
            continue
        notes = insertions[row["ref_pitch"]]
        left = bisect_left(notes, (onset - window_sec, -1))
        right = bisect_right(notes, (onset + window_sec, len(rows)))
        for played, other in notes[left:right]:
            distance = abs(played - onset)
            if distance <= window_sec and any(abs(played - anchor) <= window_sec for anchor in nearby_anchors):
                # Resolve ties in the source-note order used by the experiment.
                candidates.append((
                    distance, onset, row["ref_onset_tick"] or 0, row["ref_id"],
                    played, rows[other]["performance_id"], index, other,
                ))

    replacements = {}
    consumed = set()
    for *_, index, other in sorted(candidates):
        if index in replacements or other in consumed:
            continue
        performance = rows[other]
        replacements[index] = {
            **rows[index],
            **{key: value for key, value in performance.items() if key.startswith("performance_")},
            "alignment_type": "match",
        }
        consumed.add(other)
    return [replacements.get(index, row) for index, row in enumerate(rows) if index not in consumed]
