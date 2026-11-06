"""Use artifact versions verbatim; unversioned artifacts are legacy v1.1."""
from ..alignment.feature_extraction import resolve_kc_version


def result_kc_version(result):
    version = result.get('kc_labeler_version', 'v1.1')
    if not isinstance(version, str) or not version:
        raise ValueError('Invalid kc_labeler_version')
    return version


def dataset_kc_version(results):
    versions = {result_kc_version(result) for result in results}
    if len(versions) > 1:
        raise ValueError(f'Mixed KC versions are not supported: {sorted(versions)}')
    return next(iter(versions), resolve_kc_version())
