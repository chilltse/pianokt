"""Preserve nullable integer domains at the DataFrame → JSON boundary."""
import math
import pandas as pd
from .feature_extraction import ReferenceGroupingMismatch

INTEGER_NOTE_FIELDS = frozenset({
    'ref_pitch', 'performance_pitch', 'ref_track', 'performance_track',
    'ref_onset_tick', 'performance_onset_tick', 'ref_channel', 'performance_channel',
    'ref_velocity', 'performance_velocity', 'reference_chord_index',
    'performance_chord_index', 'reject_reason',
})


def alignment_records(frame):
    records = frame.to_dict(orient='records')
    for index, row in enumerate(records):
        for key, value in row.items():
            if pd.isna(value):
                row[key] = None
            elif key in INTEGER_NOTE_FIELDS:
                if isinstance(value, bool) or not math.isfinite(value) or int(value) != value:
                    raise ReferenceGroupingMismatch(f'Non-integral {key} in alignment row={index}: {value}')
                row[key] = int(value)
    return records
