import hashlib
import json
from dataclasses import dataclass, asdict

def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()

def stable_id(*parts):
    return digest(json.dumps(parts,sort_keys=True,separators=(',',':')).encode())

@dataclass(frozen=True)
class AlignmentConfig:
    version: str = 'dual-dtw-global-offset-v1'
    timing_threshold_ms: int = 150
    insertion_attach_window_sec: float = 0.25
    minimum_anchors: int = 3
    max_notes: int = 10000

    @property
    def identity(self):
        return asdict(self)

def check_midi(path):
    if path.stat().st_size > 8_000_000:
        raise ValueError('MIDI exceeds 8 MB')
    with path.open('rb') as f:
        if f.read(4) != b'MThd':
            raise ValueError('Invalid MIDI header')
