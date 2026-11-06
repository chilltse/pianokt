"""Explicit version registry. Unknown versions never fall back to default."""
from dataclasses import dataclass
from typing import Callable
from .types import ReferenceFeatures
from .versions import v1_2_0


@dataclass(frozen=True)
class FeatureExtractor:
    version: str
    feature_names: tuple[str, ...]
    extract: Callable[..., ReferenceFeatures]


DEFAULT_KC_LABELER_VERSION = v1_2_0.KC_LABELER_VERSION
_REGISTRY = {
    v1_2_0.KC_LABELER_VERSION: FeatureExtractor(
        v1_2_0.KC_LABELER_VERSION, tuple(v1_2_0.KC_FEATURE_NAMES),
        v1_2_0.extract_reference_features),
}


def get_extractor(version: str | None = None) -> FeatureExtractor:
    selected = DEFAULT_KC_LABELER_VERSION if version is None else version
    if selected not in _REGISTRY:
        raise ValueError(f"Unsupported kc_labeler_version={selected!r}; available={list(_REGISTRY)}")
    return _REGISTRY[selected]


def available_versions() -> tuple[str, ...]:
    return tuple(_REGISTRY)
