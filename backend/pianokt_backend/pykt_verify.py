"""Verify a training-owned golden fixture before issuing a compatibility manifest.

python -m pianokt_backend.pykt_verify fixture.json --url http://localhost:8000
"""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path

import httpx

from .pykt_integration import Compatibility, CompatibilityError, PyKTClient, ReferenceCandidates, MODEL_FIELDS, canonical


def verify_fixture(path, http):
    fixture = json.loads(path.read_text())
    reference = path.parent / fixture['reference_midi']
    if sha256(reference.read_bytes()).hexdigest() != fixture['reference_sha256']:
        raise CompatibilityError('Golden reference hash mismatch')
    if not fixture.get('training_source'):
        raise CompatibilityError('Training provenance is required')
    rows = ReferenceCandidates().extract(reference, song_id=fixture['song_id'],
        song_version=fixture['song_version'], kc_labeler_version=fixture['kc_labeler_version'],
        **fixture.get('segment', {}))
    # Compare JSON, not Python equality: bool/int/float must not collapse.
    actual = [dict(onset_index=r['onset_index'], onset_time=r['onset_time'], kc_vector=r['kc_vector']) for r in rows]
    expected = [{k: r[k] for k in ('onset_index', 'onset_time', 'kc_vector')} for r in fixture['onsets']]
    if (not actual or len(actual) != len(expected) or any(
            a['onset_index'] != e['onset_index']
            or not isfinite(e['onset_time']) or abs(a['onset_time'] - e['onset_time']) > 1e-6
            or canonical(a['kc_vector']) != canonical(e['kc_vector'])
            for a, e in zip(actual, expected))):
        raise CompatibilityError('Training KC/onsets differ from current reference labeler; inference prohibited')
    contract = Compatibility(fixture['model_version'], fixture['feature_version'],
                             fixture['kc_labeler_version'], sha256(path.read_bytes()).hexdigest())
    client = PyKTClient(http, contract)
    # A dedicated empty diagnostic identity, never a real learner or observe call.
    student = 'pianokt-golden-' + contract.evidence_sha256[:24]
    client.inspect()
    before = client._state(student)
    if before['history_revision'] != 0 or before['next_position'] != 0:
        raise CompatibilityError('Golden diagnostic student must have empty history')
    result = client.predict(student, rows)
    for row, golden in zip(result['onsets'], fixture['onsets'], strict=True):
        for key in ('attrs', 'pid', 'unknown_fields', 'unknown_question'):
            if canonical(row[key]) != canonical(golden[key]):
                raise CompatibilityError(f'Golden encoding mismatch: {key}')
        if (not isfinite(golden['probability']) or not 0 <= golden['probability'] <= 1
                or abs(row['probability'] - golden['probability']) > 1e-6):
            raise CompatibilityError('Golden empty-history probability mismatch')
    duplicate = client.predict(student, [dict(rows[0], song_id='duplicate-song'), rows[0]])
    if duplicate['onsets'][0]['probability'] != duplicate['onsets'][1]['probability']:
        raise CompatibilityError('Same KC differs across songs')
    # Encoder contract probes are deliberately raw JSON, outside song validation.
    vectors = [[False, 1, False, None, 1, None], [0, 1, 0, None, 1, None],
               [False, 999999, False, None, 1, None], [None] * 6,
               [False, 1, False, None, 1, 0]]
    probes = client._request('POST', f'/kt/students/{student}/predict', json=dict(
        kc_feature_names=list(MODEL_FIELDS), candidates=[dict(candidate_id=str(i), kc_vector=v)
                                                       for i, v in enumerate(vectors)]))
    encoded = {p['candidate_id']: p for p in probes['predictions']}
    if (probes['state'] != before or encoded['0']['attrs'][0] == encoded['1']['attrs'][0]
            or encoded['0']['attrs'][2] == encoded['1']['attrs'][2]
            or encoded['2']['attrs'][1] != 1 or encoded['2']['pid'] != 0
            or not encoded['2']['unknown_question'] or 'kc_1' not in encoded['2']['unknown_fields']
            or encoded['3']['attrs'] != [2] * 6 or encoded['3']['unknown_fields']
            or encoded['0']['attrs'][5] != 2 or encoded['4']['attrs'][5] == 2):
        raise CompatibilityError('JSON type / MISSING / UNK / pid=0 contract mismatch')
    client.rollout(student, rows[:2], seed=7)
    if client._state(student) != before:
        raise CompatibilityError('Golden checks changed student state')
    return asdict(contract)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get('PYKT_API_TOKEN')
    with httpx.Client(base_url=args.url, timeout=60, trust_env=False, headers={'Authorization': f'Bearer {token}'} if token else {}) as http:
        contract = verify_fixture(args.fixture, http)
    args.output.write_text(json.dumps(contract, indent=2) + '\n')


if __name__ == '__main__':
    main()
