"""Canonical alignment, independent of alignment and KC policies."""
import json
from importlib.resources import files

DEFAULT_ALIGNMENT_VERSION = "1.0.0"


def resolve_alignment_version(version=None):
    version = DEFAULT_ALIGNMENT_VERSION if version is None else version
    if version != DEFAULT_ALIGNMENT_VERSION:
        raise ValueError(f"Unsupported alignment version: {version}")
    return version


def load_alignment_policy(version=None):
    resolve_alignment_version(version)
    return json.loads(files(__package__).joinpath("versions/v1_0_0.json").read_text())

