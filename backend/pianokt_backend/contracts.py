import hashlib
import json
from dataclasses import dataclass, asdict

def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()

def stable_id(*parts):
    return digest(json.dumps(parts,sort_keys=True,separators=(',',':')).encode())

from .alignment.alignment_policies import load_alignment_policy, resolve_alignment_version
from .alignment.assessment_policies import load_assessment_policy
from .alignment.reference_grouping import load_reference_grouping_policy

_POLICY = load_alignment_policy()
_GROUPING = load_reference_grouping_policy()

@dataclass(frozen=True)
class AlignmentConfig:
    # Challenge recordings have a shared player-song-time clock.  This version
    # intentionally replaces unconstrained full-score DTW with a local,
    # chord-aware, monotonic matcher.
    version: str = _POLICY['version']
    timing_threshold_ms: int = load_assessment_policy()['timing']['max_tolerance_ms']
    insertion_attach_window_sec: float = _POLICY['extra_note_attachment_ms'] / 1000
    assessment_version: str = '1.0.0'
    minimum_anchors: int = 3
    max_notes: int = 10000
    chord_onset_tolerance_sec: float = _GROUPING['onset_grouping_ms'] / 1000
    match_window_sec: float = _POLICY['match_window_ms'] / 1000
    wrong_pitch_window_sec: float = _POLICY['wrong_pitch_window_ms'] / 1000
    performance_chord_grouping_sec: float = _POLICY['performance_chord_grouping_ms'] / 1000
    reference_grouping_version: str = _GROUPING['version']
    max_alignment_cells: int = 2_000_000
    calibration_max_mad_sec: float = 0.5
    calibration_max_offset_sec: float = 1.0
    telemetry_midi_onset_tolerance_sec: float = 0.04
    transport_monotonic_tolerance_sec: float = 0.03

    additive_rescue_window_sec: float = _POLICY["additive_rescue_window_ms"] / 1000

    def __post_init__(self):
        resolve_alignment_version(self.version)
        load_assessment_policy(self.assessment_version)
        load_reference_grouping_policy(self.reference_grouping_version)
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and (value < 0 or not __import__("math").isfinite(value)):
                raise ValueError(f"Invalid alignment configuration: {key}")


    @property
    def identity(self):
        data = asdict(self)
        data["assessment_policy"] = load_assessment_policy(self.assessment_version)
        return data

def check_midi(path):
    if path.stat().st_size > 8_000_000:
        raise ValueError('MIDI exceeds 8 MB')
    with path.open('rb') as f:
        if f.read(4) != b'MThd':
            raise ValueError('Invalid MIDI header')
