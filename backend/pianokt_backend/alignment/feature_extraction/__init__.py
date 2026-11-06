"""Public KC API: callers select versions without importing implementations."""
from .registry import available_versions, get_extractor
from .types import ReferenceFeatures, ReferenceGroupingMismatch

# Compatibility aliases for the current default; artifact metadata comes from
# the selected extractor, never from these aliases.
KC_LABELER_VERSION = get_extractor().version
KC_FEATURE_NAMES = list(get_extractor().feature_names)


def resolve_kc_version(version: str | None = None) -> str:
    return get_extractor(version).version


def extract_reference_features(reference_chords, hand_mapping, *, kc_labeler_version=None):
    extractor = get_extractor(kc_labeler_version)
    result = extractor.extract(reference_chords, hand_mapping)
    if (result.kc_labeler_version != extractor.version
            or result.kc_feature_names != extractor.feature_names):
        raise ReferenceGroupingMismatch("Extractor metadata disagrees with registered version")
    if any(len(event["vector"]) != len(extractor.feature_names) for event in result.events):
        raise ReferenceGroupingMismatch("KC vector dimension disagrees with registered features")
    return result
