"""Reference-only PyKT HTTP integration. No model weights or vocabulary IDs here."""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from urllib.parse import quote

import httpx

from .alignment.feature_extraction.reference import extract_midi_reference
from .alignment.feature_extraction.versions.v1_2_0 import KC_FEATURE_NAMES

MODEL_FIELDS = tuple(f"kc_{i}" for i in range(6))
FIELD_MAPPING = tuple(zip(KC_FEATURE_NAMES, MODEL_FIELDS))


class CompatibilityError(ValueError):
    """Inference must stop until the deployment/training contract is verified."""


def canonical(value):
    # JSON identity distinguishes False from 0 and None from 0 (Python tuples do not).
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_vector(vector):
    if not isinstance(vector, list) or len(vector) != 6:
        raise CompatibilityError("Expected six raw KC fields")
    for i, value in enumerate(vector):
        if value is None and i in (3, 5):
            continue
        expected = bool if i in (0, 2) else int
        if type(value) is not expected:
            raise CompatibilityError(f"Invalid JSON type for {KC_FEATURE_NAMES[i]}")
    if vector[1] < 1 or vector[4] < 0:
        raise CompatibilityError("Invalid note/change count")
    if vector[3] not in (None, 10, 30, 50, 70, 90) or vector[5] not in (None, 0, 1, 2, 3, 4):
        raise CompatibilityError("Unknown KC discretization")


@dataclass(frozen=True)
class Compatibility:
    """Pinned, externally audited training semantics; never infer from kc_N names."""
    model_version: str
    feature_version: str
    kc_labeler_version: str
    evidence_sha256: str

    def check(self, info):
        if (self.kc_labeler_version != "1.2.0" or len(self.evidence_sha256) != 64
                or any(c not in '0123456789abcdef' for c in self.evidence_sha256)):
            raise CompatibilityError("A verified KC 1.2.0 golden fixture is required")
        if (info.get("model_type") != "akt_fmkc"
                or info.get("kc_feature_names") != list(MODEL_FIELDS)
                or info.get("model_version") != self.model_version
                or info.get("feature_version") != self.feature_version):
            raise CompatibilityError("Deployment differs from verified training contract; revalidate")


class ReferenceCandidates:
    """Instance-local cache; full reference is extracted before [start, end) slicing."""
    def __init__(self):
        self._cache = {}

    def extract(self, reference: Path, *, song_id: str, song_version: str,
                segment_id: str | None = None, start: float = 0, end: float | None = None,
                kc_labeler_version: str = "1.2.0"):
        if not song_id or not song_version or (segment_id is not None and not segment_id):
            raise ValueError("Song identity and version are required")
        if not isfinite(start) or start < 0 or (end is not None and (not isfinite(end) or end <= start)):
            raise ValueError("Invalid segment bounds")
        if segment_id is None and (start != 0 or end is not None):
            raise ValueError("Slicing requires segment_id")
        content_hash = sha256(reference.read_bytes()).hexdigest()
        key = (content_hash, kc_labeler_version)
        if key not in self._cache:
            artifact = extract_midi_reference(reference, kc_labeler_version=kc_labeler_version)
            if artifact['midi_sha256'] != content_hash:
                raise CompatibilityError("Reference changed during extraction")
            if artifact['events_data']['kc_feature_names'] != KC_FEATURE_NAMES:
                raise CompatibilityError("Unexpected feature ordering")
            self._cache[key] = artifact
        data = self._cache[key]['events_data']
        rows = []
        for index, (time, vector) in enumerate(zip(data['onset_seconds'], data['kc_vectors'], strict=True)):
            validate_vector(vector)
            if time >= start and (end is None or time < end):
                rows.append(dict(song_id=song_id, segment_id=segment_id, song_version=song_version,
                    reference_sha256=content_hash, kc_labeler_version=kc_labeler_version,
                    onset_index=index, onset_time=time, kc_vector=deepcopy(vector)))
        return rows


class PyKTClient:
    """Only read-state, predict and non-retained rollout endpoints are exposed.

    The caller owns and closes the httpx.Client, including its auth/timeout config.
    Prediction caches are local to this service connection and authorization scope.
    """
    def __init__(self, http: httpx.Client, compatibility: Compatibility):
        self.http = http
        self.compatibility = compatibility
        self._predictions = {}

    def _request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def inspect(self):
        spec = self._request('GET', '/openapi.json')
        required = {'/kt/model/info': 'get', '/kt/students/{student_id}/state': 'get',
                    '/kt/students/{student_id}/predict': 'post', '/kt/rollouts/evaluate': 'post'}
        if any(method not in spec.get('paths', {}).get(path, {}) for path, method in required.items()):
            raise CompatibilityError("Required deployed OpenAPI operations are missing")
        info = self._request('GET', '/kt/model/info')
        self.compatibility.check(info)
        return info

    def _state(self, student_id):
        if not isinstance(student_id, str) or not student_id or len(student_id) > 256:
            raise ValueError("Invalid student_id")
        state = self._request('GET', f'/kt/students/{quote(student_id, safe="")}/state')
        if (state.get('student_id') != student_id or type(state.get('history_revision')) is not int
                or state['history_revision'] < 0
                or state.get('model_version') != self.compatibility.model_version
                or state.get('feature_version') != self.compatibility.feature_version):
            raise CompatibilityError("Student state/version mismatch")
        return state

    def _validate_rows(self, rows):
        for row in rows:
            if row.get('kc_labeler_version') != self.compatibility.kc_labeler_version:
                raise CompatibilityError("Candidate labeler version mismatch")
            for key in ('song_id', 'song_version', 'reference_sha256', 'onset_index', 'onset_time', 'segment_id'):
                if key not in row:
                    raise CompatibilityError(f"Candidate missing {key}")
            validate_vector(row['kc_vector'])

    def predict(self, student_id, rows):
        self._validate_rows(rows)
        info = self.inspect()  # Recheck even for cache hits.
        state = self._state(student_id)
        prefix = (student_id, canonical(state), self.compatibility.kc_labeler_version,
                  self.compatibility.evidence_sha256)
        unique = {canonical(row['kc_vector']): row['kc_vector'] for row in rows}
        missing = [key for key in unique if (*prefix, key) not in self._predictions]
        limit = info.get('limits', {}).get('max_candidates')
        if type(limit) is not int or limit <= 0:
            raise CompatibilityError("Missing service candidate limit")
        pending = {}
        for offset in range(0, len(missing), min(limit, 4096)):
            keys = missing[offset:offset + min(limit, 4096)]
            candidates = [dict(candidate_id=str(i), kc_vector=unique[key]) for i, key in enumerate(keys)]
            result = self._request('POST', f'/kt/students/{quote(student_id, safe="")}/predict',
                                   json=dict(kc_feature_names=list(MODEL_FIELDS), candidates=candidates))
            if result.get('state') != state:
                raise CompatibilityError("Student state changed during prediction; retry")
            predictions = result['predictions']
            by_id = {p['candidate_id']: p for p in predictions}
            if len(by_id) != len(predictions) or set(by_id) != {c['candidate_id'] for c in candidates}:
                raise CompatibilityError("Prediction IDs do not match request")
            for i, key in enumerate(keys):
                p = deepcopy(by_id[str(i)])
                p.pop('candidate_id')
                if (type(p.get('probability')) not in (int, float) or not isfinite(p['probability'])
                        or not 0 <= p['probability'] <= 1
                        or type(p.get('pid')) is not int or p['pid'] < 0
                        or len(p.get('attrs', [])) != 6
                        or any(type(a) is not int or a < 1 for a in p['attrs'])
                        or type(p.get('unknown_question')) is not bool
                        or p['unknown_question'] != (p['pid'] == 0)
                        or p.get('unknown_fields') != [n for n, a in zip(MODEL_FIELDS, p['attrs']) if a == 1]):
                    raise CompatibilityError("Invalid PyKT encoding/prediction response")
                pending[(*prefix, key)] = p
        if self._state(student_id) != state:
            raise CompatibilityError("Student state changed during prediction; retry")
        self._predictions.update(pending)
        output = [dict(deepcopy(row), **deepcopy(self._predictions[(*prefix, canonical(row['kc_vector']))])) for row in rows]
        count = len(output)
        return dict(state=state, kc_labeler_version=self.compatibility.kc_labeler_version,
                    evidence_sha256=self.compatibility.evidence_sha256, onsets=output,
                    unknown_question_ratio=sum(p['unknown_question'] for p in output) / count if count else 0,
                    unknown_field_ratio=sum(len(p['unknown_fields']) for p in output) / (6 * count) if count else 0)

    def rollout(self, student_id, rows, *, mode='sample', responses=None, seed=0, num_trajectories=1):
        self._validate_rows(rows)
        self.inspect()
        state = self._state(student_id)
        if mode not in ('sample', 'forced') or not rows:
            raise ValueError("Nonempty sequence and sample/forced mode required")
        if mode == 'forced':
            if responses is None or len(responses) != len(rows) or any(type(r) is not int or r not in (0, 1) for r in responses):
                raise ValueError("Forced rollout requires one binary integer response per onset")
        elif responses is not None:
            raise ValueError("Sample rollout does not accept responses")
        sequence = [dict(kc_vector=row['kc_vector'], **({'response': responses[i]} if mode == 'forced' else {}))
                    for i, row in enumerate(rows)]
        result = self._request('POST', '/kt/rollouts/evaluate', json=dict(student_id=student_id,
            kc_feature_names=list(MODEL_FIELDS), sequence=sequence, mode=mode, seed=seed,
            num_trajectories=num_trajectories, retain_branch=False, include_steps=True))
        if (result.get('state') != state or result.get('base_revision') != state['history_revision']
                or result.get('branch') is not None or self._state(student_id) != state):
            raise CompatibilityError("Rollout state/version mismatch")
        return dict(result, kc_labeler_version=self.compatibility.kc_labeler_version,
                    evidence_sha256=self.compatibility.evidence_sha256, onsets=deepcopy(rows))
