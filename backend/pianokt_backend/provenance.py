"""Server-owned provenance. Missing configuration must never label data production."""
import os

ENVIRONMENTS = ('development', 'test', 'staging', 'production')


def recording_environment():
    value = os.getenv('PIANOKT_ENV', 'development')
    if value not in ENVIRONMENTS:
        raise ValueError('PIANOKT_ENV must be development, test, staging or production')
    return value


def export_environment(value):
    if value not in ENVIRONMENTS:
        raise ValueError('Export requires an explicit known source environment')
    return value
