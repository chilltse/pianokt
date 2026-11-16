"""Shared producer/synchronizer boundary contract, in reference-relative seconds.

Only silence may be omitted. Every sounding note must fit entirely within one
segment. Tolerance is exclusively for floating-point arithmetic (one microsecond).
"""
import math

EPSILON_SEC = 1e-6


def validate_segment_bounds(notes, bounds):
    notes = [(float(n.start), float(n.end)) for n in notes]
    bounds = [(float(a), float(b)) for a, b in bounds]
    if not notes or not bounds:
        raise ValueError('Reference notes and segment bounds are required')
    if any(not math.isfinite(x) for pair in notes + bounds for x in pair):
        raise ValueError('Non-finite note or segment boundary')
    if any(a < 0 or b <= a for a, b in notes + bounds):
        raise ValueError('Invalid note or segment boundary')
    if any(a < previous_end - EPSILON_SEC for (_, previous_end), (a, _) in zip(bounds, bounds[1:])):
        raise ValueError('Segments overlap or are unordered')
    for start, end in notes:
        if not any(a <= start + EPSILON_SEC and end <= b + EPSILON_SEC for a, b in bounds):
            raise ValueError('Segment boundaries omit or truncate a reference note')
