"""Pinned, local-on-disk PianoKT recommendation KC features.

The cache is a *feature* cache, not an attestation of standalone musical context.
CatalogCandidateProvider must still check its externally audited provenance.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from uuid import UUID

KC_VERSION = '1.2.0'
KC_FIELDS = (
    'has_black_key', 'simultaneous_note_count', 'both_hands',
    'onset_interval_bin_cs', 'change_count', 'mean_move_bin',
)
FINGERPRINT_FILES = (
    'alignment/feature_extraction/reference.py',
    'alignment/feature_extraction/__init__.py',
    'alignment/feature_extraction/registry.py',
    'alignment/feature_extraction/versions/v1_2_0.py',
    'alignment/hands.py',
    'alignment/time_guided.py',
    'contracts.py',
)
SHA64 = re.compile(r'[0-9a-f]{64}\Z')


class CandidateCacheError(ValueError):
    """Safe opaque status code for candidate-specific cache errors."""


def implementation_fingerprint(package_root: Path | None = None) -> str:
    """Fail closed when code defining grouping, hands, or KC changes."""
    root = package_root or Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for rel in FINGERPRINT_FILES:
        path = root / rel
        if not path.is_file():
            raise CandidateCacheError('candidate_cache_missing_implementation')
        digest.update(rel.encode('utf-8') + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def validate_vector(vector):
    if not isinstance(vector, list) or len(vector) != len(KC_FIELDS):
        raise CandidateCacheError('candidate_cache_bad_vector')
    for idx, value in enumerate(vector):
        if idx in (3, 5) and value is None:
            continue
        typ = bool if idx in (0, 2) else int
        if type(value) is not typ:
            raise CandidateCacheError('candidate_cache_bad_vector')
    if (vector[1] < 1 or vector[4] < 0 or
            vector[3] not in (None, 10, 30, 50, 70, 90) or
            vector[5] not in (None, 0, 1, 2, 3, 4)):
        raise CandidateCacheError('candidate_cache_bad_vector')


def artifact_to_candidate(record: dict, reference_artifact: dict, fingerprint: str) -> dict:
    """Transform validated full extract-reference artifact to small serving snapshot."""
    data = reference_artifact['events_data']
    rows = []
    for ix, (onset, vector) in enumerate(zip(data['onset_seconds'], data['kc_vectors'], strict=True)):
        validate_vector(vector)
        if type(onset) not in (float, int) or not math.isfinite(onset) or onset < 0:
            raise CandidateCacheError('candidate_cache_bad_onset')
        if rows and onset <= rows[-1]['onset_time']:
            raise CandidateCacheError('candidate_cache_bad_onset')
        rows.append({'onset_index': ix, 'onset_time': float(onset), 'kc_vector': vector})
    if not rows or len(rows) != reference_artifact['reference_events_count']:
        raise CandidateCacheError('candidate_cache_empty')
    return {
        'artifact_type': 'pianokt_recommendation_kc',
        'schema_version': 1,
        'kc_labeler_version': KC_VERSION,
        'kc_feature_names': list(KC_FIELDS),
        'implementation_sha256': fingerprint,
        'song_id': record['song_id'],
        'segment_id': record['segment_id'],
        'segment_index': record['segment_index'],
        'midi_sha256': record['midi_sha256'],
        'reference_events_count': len(rows),
        'onsets': rows,
    }


def validate_candidate(candidate: dict, record: dict, fingerprint: str) -> list[dict]:
    try:
        song_id = str(UUID(str(record['song_id'])))
        segment_id = str(UUID(str(record['segment_id'])))
        sha = record['midi_sha256']
        segment_index = record['segment_index']
    except (KeyError, ValueError, TypeError):
        raise CandidateCacheError('candidate_cache_identity_mismatch') from None
    if (type(segment_index) is not int or segment_index < 1
            or not isinstance(sha, str) or not SHA64.fullmatch(sha)):
        raise CandidateCacheError('candidate_cache_identity_mismatch')
    if (not isinstance(candidate, dict)
            or candidate.get('artifact_type') != 'pianokt_recommendation_kc'
            or candidate.get('schema_version') != 1
            or candidate.get('kc_labeler_version') != KC_VERSION
            or candidate.get('kc_feature_names') != list(KC_FIELDS)
            or candidate.get('implementation_sha256') != fingerprint):
        raise CandidateCacheError('candidate_cache_version_mismatch')
    if (candidate.get('song_id') != song_id or candidate.get('segment_id') != segment_id
            or candidate.get('segment_index') != segment_index or candidate.get('midi_sha256') != sha):
        raise CandidateCacheError('candidate_cache_identity_mismatch')
    onsets = candidate.get('onsets')
    if (not isinstance(onsets, list) or not onsets or len(onsets) > 500_000
            or len(onsets) != candidate.get('reference_events_count')):
        raise CandidateCacheError('candidate_cache_empty')
    rows = []
    last = -1.0
    for ix, onset in enumerate(onsets):
        if not isinstance(onset, dict) or onset.get('onset_index') != ix or type(onset.get('onset_index')) is not int:
            raise CandidateCacheError('candidate_cache_bad_onset')
        time = onset.get('onset_time')
        if type(time) not in (int, float) or not math.isfinite(time) or time < 0 or time <= last:
            raise CandidateCacheError('candidate_cache_bad_onset')
        last = time
        vec = onset.get('kc_vector')
        validate_vector(vec)
        rows.append({
            'song_id': song_id, 'segment_id': segment_id, 'song_version': sha,
            'reference_sha256': sha, 'kc_labeler_version': KC_VERSION,
            'onset_index': ix, 'onset_time': time, 'kc_vector': vec,
        })
    return rows


class LocalCandidateFeatureCache:
    """Serve *only* verified published segment identities. Never open arbitrary paths."""

    def __init__(self, root: Path | str, supabase_url: str):
        self.root = Path(root).expanduser().resolve()
        manifest_path = self.root / 'recommendation_features' / 'manifest.json'
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        except (OSError, ValueError):
            raise CandidateCacheError('candidate_cache_missing_manifest') from None
        self.fingerprint = implementation_fingerprint()
        if (manifest.get('schema_version') != 1
                or manifest.get('kc_labeler_version') != KC_VERSION
                or manifest.get('kc_feature_names') != list(KC_FIELDS)
                or manifest.get('implementation_sha256') != self.fingerprint):
            raise CandidateCacheError('candidate_cache_version_mismatch')
        if manifest.get('supabase_origin') != supabase_url.rstrip('/'):
            raise CandidateCacheError('candidate_cache_origin_mismatch')

    def load(self, record: dict) -> list[dict]:
        try:
            sid = str(UUID(str(record['segment_id'])))
            sha = record['midi_sha256']
        except (ValueError, TypeError, KeyError):
            raise CandidateCacheError('candidate_cache_identity_mismatch') from None
        if not isinstance(sha, str) or not SHA64.fullmatch(sha):
            raise CandidateCacheError('candidate_cache_identity_mismatch')
        target = self.root / 'recommendation_features' / sid / f'{sha}.json'
        try:
            if target.stat().st_size > 30 * 1024 * 1024:
                raise CandidateCacheError('candidate_cache_too_large')
            candidate = json.loads(target.read_text(encoding='utf8'))
        except (ValueError, OSError):
            raise CandidateCacheError('candidate_cache_missing_or_invalid') from None
        return validate_candidate(candidate, record, self.fingerprint)
