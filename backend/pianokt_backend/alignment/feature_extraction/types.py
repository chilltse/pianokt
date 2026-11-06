"""Version-independent Reference/response join contract."""
from dataclasses import dataclass
from typing import Any
from ..time_guided import MidiNote


class ReferenceGroupingMismatch(RuntimeError):
    """Deterministic internal Reference/Alignment contract violation."""


@dataclass(frozen=True)
class ReferenceFeatures:
    groups: tuple[dict[str, MidiNote], ...]
    events: tuple[dict[str, Any], ...]
    kc_labeler_version: str
    kc_feature_names: tuple[str, ...]
