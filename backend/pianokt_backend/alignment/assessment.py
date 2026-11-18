"""Assessment of fixed note correspondence; never changes identities or onsets."""
import math
from .assessment_policies import validate_policy

ASSESSMENT_IMPLEMENTATION = 'note-ioi-event-accuracy-v1'


def assess_tolerances(frame, onsets, policy):
    validate_policy(policy)
    timing = policy['timing']
    intervals = [1000 * (b - a) for a, b in zip(onsets, onsets[1:])]
    if any(not math.isfinite(x) or x <= 0 for x in intervals):
        raise ValueError('Reference IOI must be finite and positive')
    tolerances = []
    for i in range(len(onsets)):
        previous = intervals[i-1] if i else (intervals[0] if intervals else None)
        following = intervals[i] if i < len(intervals) else previous
        tolerances.append(tuple(timing['max_tolerance_ms'] if timing['mode'] == 'fixed' or x is None
                                else min(timing['max_tolerance_ms'], timing['ioi_ratio'] * x)
                                for x in (previous, following)))
    result = frame.copy()
    for side, index in (('early', 0), ('late', 1)):
        result[side + '_tolerance_ms'] = [tolerances[int(i)][index] if kind != 'insertion' else None
            for i, kind in zip(result.reference_chord_index, result.alignment_type)]
    return result
