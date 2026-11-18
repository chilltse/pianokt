"""Canonical reference grouping, independent of alignment and KC policies."""
import json
from importlib.resources import files

DEFAULT_REFERENCE_GROUPING_VERSION = "1.0.0"


def resolve_reference_grouping_version(version=None):
    version = DEFAULT_REFERENCE_GROUPING_VERSION if version is None else version
    if version != DEFAULT_REFERENCE_GROUPING_VERSION:
        raise ValueError(f"Unsupported reference grouping version: {version}")
    return version


def load_reference_grouping_policy(version=None):
    resolve_reference_grouping_version(version)
    return json.loads(files(__package__).joinpath("versions/v1_0_0.json").read_text())


def group_reference_onsets(notes, reference_grouping_version=None):
    from ..time_guided import group_chords
    policy = load_reference_grouping_policy(reference_grouping_version)
    return group_chords(notes, policy["onset_grouping_ms"] / 1000)
