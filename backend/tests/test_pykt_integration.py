import json
from dataclasses import replace

import httpx
import pytest

from pianokt_backend.pykt_integration import (
    Compatibility, CompatibilityError, MODEL_FIELDS, PyKTClient, ReferenceCandidates,
    canonical, validate_vector,
)

CONTRACT = Compatibility('model-v1', 'features-v1', '1.2.0', 'a' * 64)


def row(vector=None, **changes):
    return dict(song_id='song', song_version='v1', segment_id=None, reference_sha256='b'*64,
                onset_index=0, onset_time=0., kc_labeler_version='1.2.0',
                kc_vector=vector or [False, 1, False, None, 1, None], **changes)


class Service:
    def __init__(self):
        self.revision = 0
        self.model = 'model-v1'
        self.feature = 'features-v1'
        self.posts = []
        self.race = False

    def state(self):
        return dict(student_id='student', history_revision=self.revision, next_position=self.revision,
                    cache_length=self.revision, model_version=self.model, feature_version=self.feature)

    def __call__(self, req):
        if req.url.path == '/openapi.json':
            data = {'paths': {p: {m: {}} for p, m in [('/kt/model/info', 'get'),
                ('/kt/students/{student_id}/state', 'get'), ('/kt/students/{student_id}/predict', 'post'),
                ('/kt/rollouts/evaluate', 'post')]}}
        elif req.url.path == '/kt/model/info':
            data = dict(model_type='akt_fmkc', model_version=self.model, feature_version=self.feature,
                        kc_feature_names=list(MODEL_FIELDS), limits={'max_candidates': 1})
        elif req.url.path.endswith('/state'):
            data = self.state()
        else:
            assert req.method == 'POST'
            body = json.loads(req.content)
            self.posts.append((req.url.path, body))
            assert body['kc_feature_names'] == list(MODEL_FIELDS)
            if req.url.path.endswith('/predict'):
                data = dict(state=self.state(), predictions=[dict(candidate_id=c['candidate_id'], probability=.7,
                    attrs=[3, 1, 3, 2, 3, 2], pid=0, unknown_fields=['kc_1'], unknown_question=True)
                    for c in body['candidates']])
                if self.race:
                    self.revision += 1
            else:
                assert req.url.path == '/kt/rollouts/evaluate'
                assert body['retain_branch'] is False
                data = dict(state=self.state(), base_revision=self.revision, branch=None)
        return httpx.Response(200, json=data)


def client(service):
    return PyKTClient(httpx.Client(base_url='http://test', transport=httpx.MockTransport(service)), CONTRACT)


def test_duplicates_types_null_unknown_and_readonly():
    service = Service()
    c = client(service)
    rows = [row(), dict(row(), song_id='other'), row([True, 2, True, 30, 1, 2])]
    result = c.predict('student', rows)
    assert len(result['onsets']) == 3
    assert len(service.posts) == 2  # service limit=1, two unique vectors
    assert result['unknown_question_ratio'] == 1
    assert result['unknown_field_ratio'] == 1/6
    assert result['onsets'][0]['probability'] == result['onsets'][1]['probability']
    assert service.posts[0][1]['candidates'][0]['kc_vector'] == rows[0]['kc_vector']
    assert type(service.posts[0][1]['candidates'][0]['kc_vector'][0]) is bool
    result['onsets'][0]['attrs'][0] = 999
    assert c.predict('student', rows)['onsets'][0]['attrs'][0] == 3
    assert len(service.posts) == 2
    assert service.revision == 0


@pytest.mark.parametrize('field', ['model', 'feature'])
def test_changed_deployment_rejects_cached_results(field):
    s = Service(); c = client(s)
    c.predict('student', [row()])
    setattr(s, field, 'changed')
    with pytest.raises(CompatibilityError):
        c.predict('student', [row()])
    assert len(s.posts) == 1


def test_revision_cache_and_race():
    s = Service(); c = client(s)
    c.predict('student', [row()])
    s.revision += 1
    c.predict('student', [row()])
    assert len(s.posts) == 2
    s.revision += 1; s.race = True
    with pytest.raises(CompatibilityError, match='changed'):
        c.predict('student', [row()])


def test_contract_and_types_fail_closed():
    with pytest.raises(CompatibilityError):
        replace(CONTRACT, evidence_sha256='').check({})
    for vector in ([0, 1, False, None, 1, None], [False, True, False, None, 1, None],
                   [False, 1, False, 0, 1, None], [False, 1, False, 30., 1, 1]):
        with pytest.raises(CompatibilityError):
            validate_vector(vector)
    assert canonical([False]) != canonical([0]) != canonical([None])


def test_rollout_is_nonretained_and_keeps_repetitions():
    s = Service(); c = client(s)
    c.rollout('student', [row(), row()], mode='forced', responses=[1, 0])
    body = s.posts[0][1]
    assert len(body['sequence']) == 2 and body['sequence'][1]['response'] == 0
    assert s.revision == 0
    with pytest.raises(ValueError):
        c.rollout('student', [row()], mode='forced', responses=[True])


def test_reference_full_context_and_content_cache(tmp_path):
    import mido
    path = tmp_path / 'reference.mid'
    midi = mido.MidiFile()
    track = mido.MidiTrack(); midi.tracks.append(track)
    track.append(mido.MetaMessage('track_name', name='Right Hand'))
    for pitch in (60, 62, 62):
        track.append(mido.Message('note_on', note=pitch, velocity=80, time=0))
        track.append(mido.Message('note_off', note=pitch, velocity=0, time=480))
    midi.save(path)
    cache = ReferenceCandidates()
    full = cache.extract(path, song_id='song', song_version='v1')
    segment = cache.extract(path, song_id='song', song_version='v1', segment_id='part', start=.5, end=1.5)
    assert [r['kc_vector'] for r in segment] == [r['kc_vector'] for r in full[1:]]
    assert segment[0]['kc_vector'][3] == 50
    assert segment[0]['onset_index'] == 1
    assert len(cache._cache) == 1
    full[0]['kc_vector'][0] = 'corrupt'
    assert cache.extract(path, song_id='song', song_version='v2')[0]['kc_vector'][0] is False
    track.append(mido.MetaMessage('text', text='changed')); midi.save(path)
    cache.extract(path, song_id='song', song_version='v2')
    assert len(cache._cache) == 2
    with pytest.raises(ValueError):
        cache.extract(path, song_id='song', song_version='v2', kc_labeler_version='unknown')


def test_golden_mismatch_stops_before_network(tmp_path, monkeypatch):
    from pianokt_backend import pykt_verify
    from hashlib import sha256
    path = tmp_path / 'reference.mid'; path.write_bytes(b'fixture')
    fixture = dict(reference_midi=path.name, reference_sha256=sha256(b'fixture').hexdigest(),
        training_source='training-export:example', song_id='s', song_version='v', kc_labeler_version='1.2.0',
        onsets=[dict(onset_index=0, onset_time=0., kc_vector=[0, 1, False, None, 1, None])])
    fixture_path = tmp_path / 'golden.json'; fixture_path.write_text(json.dumps(fixture))
    monkeypatch.setattr(ReferenceCandidates, 'extract', lambda *a, **kw: [row()])
    def forbidden(req):
        pytest.fail('Must not infer before training semantics match')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as http:
        with pytest.raises(CompatibilityError, match='inference prohibited'):
            pykt_verify.verify_fixture(fixture_path, http)


def test_deployed_openapi_snapshot_matches_client_contract():
    from pathlib import Path
    spec = json.loads((Path(__file__).parent / 'fixtures/pykt/deployed_openapi.json').read_text())
    schema = spec['components']['schemas']
    assert set(schema['PredictRequest']['required']) == {'kc_feature_names', 'candidates'}
    assert set(schema['Candidate']['required']) == {'candidate_id', 'kc_vector'}
    assert 'retain_branch' in schema['RolloutRequest']['properties']
    assert 'history_revision' in schema['StateInfo']['required']


def test_real_pykt_golden():
    """Opt in with a training-owned fixture; never substitute a synthetic golden."""
    import os
    from pathlib import Path
    from pianokt_backend.pykt_verify import verify_fixture
    fixture = os.environ.get('PYKT_GOLDEN_FIXTURE')
    if not fixture:
        pytest.skip('Training reference MIDI + independently recorded golden fixture required')
    token = os.environ.get('PYKT_API_TOKEN')
    with httpx.Client(base_url=os.environ.get('PYKT_URL', 'http://localhost:8000'), timeout=60, trust_env=False,
                      headers={'Authorization': f'Bearer {token}'} if token else {}) as http:
        verify_fixture(Path(fixture), http)


def test_training_golden_reference_kc_offline():
    from pathlib import Path
    from hashlib import sha256
    root = Path(__file__).parent / 'fixtures/pykt'
    fixture = json.loads((root / 'golden.json').read_text())
    reference = root / fixture['reference_midi']
    assert sha256(reference.read_bytes()).hexdigest() == fixture['reference_sha256']
    rows = ReferenceCandidates().extract(reference, song_id=fixture['song_id'],
        song_version=fixture['song_version'], **fixture['segment'])
    assert len(rows) == len(fixture['onsets']) == 129
    assert canonical([r['kc_vector'] for r in rows]) == canonical([r['kc_vector'] for r in fixture['onsets']])
    assert all(abs(r['onset_time'] - g['onset_time']) <= 1e-6
               for r, g in zip(rows, fixture['onsets'], strict=True))
