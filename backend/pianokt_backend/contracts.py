import hashlib
import json
from dataclasses import dataclass, asdict

def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()

def stable_id(*parts):
    return digest(json.dumps(parts,sort_keys=True,separators=(',',':')).encode())

@dataclass(frozen=True)
class AlignmentConfig:
    # Challenge recordings have a shared player-song-time clock.  This version
    # intentionally replaces unconstrained full-score DTW with a local,
    # chord-aware, monotonic matcher.
    version: str = 'time-guided-chord-monotonic-v3'
    timing_threshold_ms: int = 150
    insertion_attach_window_sec: float = 0.25
    minimum_anchors: int = 3
    max_notes: int = 10000
    chord_onset_tolerance_sec: float = 0.03
    match_window_sec: float = 1.25
    wrong_pitch_window_sec: float = 0.25
    max_alignment_cells: int = 2_000_000
    calibration_max_mad_sec: float = 0.5
    calibration_max_offset_sec: float = 1.0
    telemetry_midi_onset_tolerance_sec: float = 0.04
    transport_monotonic_tolerance_sec: float = 0.03

    @property
    def identity(self):
        return asdict(self)

def check_midi(path):
    if path.stat().st_size > 8_000_000:
        raise ValueError('MIDI exceeds 8 MB')
    with path.open('rb') as f:
        if f.read(4) != b'MThd':
            raise ValueError('Invalid MIDI header')
