"""Immutable assessment policies, independent of correspondence."""
import json
import math
from importlib.resources import files


def validate_policy(policy):
    if set(policy) != {'version', 'timing', 'event_accuracy'} or policy['version'] != '1.0.0':
        raise ValueError('Unsupported assessment policy')
    timing, accuracy = policy['timing'], policy['event_accuracy']
    if set(timing) != {'mode', 'ioi_ratio', 'max_tolerance_ms', 'asymmetric', 'perfect_threshold_ms'} or set(accuracy) != {'method', 'threshold'}:
        raise ValueError('Unknown or missing assessment parameters')
    if timing['mode'] not in ('fixed', 'ioi_adaptive') or timing['asymmetric'] is not True:
        raise ValueError('Unsupported timing mode or symmetry')
    for key in ('ioi_ratio', 'max_tolerance_ms', 'perfect_threshold_ms'):
        value = timing[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'Invalid {key}')
    if accuracy['method'] != 'correct_over_reference_plus_extra':
        raise ValueError('Unsupported accuracy method')
    threshold = accuracy['threshold']
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('Invalid accuracy threshold')
    return policy


def load_assessment_policy(version='1.0.0'):
    if version != '1.0.0':
        raise ValueError(f'Unsupported assessment version: {version}')
    return validate_policy(json.loads(files(__package__).joinpath('versions/v1_0_0.json').read_text()))
