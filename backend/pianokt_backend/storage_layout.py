"""Versioned learning-data namespace; catalog MIDI remains a separate domain."""
import re

DATA_ARCHITECTURE_VERSION = '3'


def _part(value):
    value = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid storage identity')
    return value


def raw_recording_key(learner_key, attempt_id):
    return f'raw/recordings/{_part(learner_key)}/{_part(attempt_id)}/performance.mid'


def raw_reference_key(learner_key, attempt_id):
    return raw_recording_key(learner_key, attempt_id).replace('/performance.mid', '/reference.mid')


def raw_telemetry_key(learner_key, attempt_id):
    return raw_recording_key(learner_key, attempt_id).replace('/performance.mid', '/performance-events.json')


def silver_result_key(run):
    return f'silver/alignment/{_part(run)}/result.json'


def silver_matches_key(run):
    return f'silver/alignment/{_part(run)}/matches.json'


def gold_dataset_prefix(kind, version, override=None):
    if kind not in ('attempts', 'knowledge_tracing'):
        raise ValueError('Unsupported dataset type')
    prefix = f'gold/{kind}/{_part(version)}'
    if override is not None and override.rstrip('/') != prefix:
        raise ValueError(f'Output must use deterministic namespace: {prefix}')
    return prefix


def gold_manifest_key(kind, version):
    return gold_dataset_prefix(kind, version) + '/manifest.json'
