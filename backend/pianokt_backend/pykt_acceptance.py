"""Offline algorithm acceptance or opt-in live diagnostic/bound-user recommendation.

The diagnostic catalog is generated, not a claim of published Supabase coverage.
"""
import argparse
from contextlib import contextmanager
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import mido

from .pykt_catalog import CatalogCandidateProvider
from .pykt_history import development
from .pykt_integration import Compatibility, CompatibilityError, PyKTClient, MODEL_FIELDS
from .pykt_recommendation import RecommendationConfig, RecommendationService

LIMITS = dict(max_candidates=256, max_rollout_steps=512, max_trajectories=32,
              max_rollout_work=8192, max_output_steps=2048, max_batch_events=512)


class DiagnosticModel:
    """Deterministic HTTP double. Used only in explicit mock acceptance/tests."""
    def __init__(self):
        self.revision = 0
        self.model = 'mock-model'
        self.feature = 'mock-features'
        self.requests = []
        self.limits = dict(LIMITS)

    def state(self, student):
        return dict(student_id=student, history_revision=self.revision, next_position=self.revision,
                    cache_length=self.revision, model_version=self.model, feature_version=self.feature)

    def probability(self, v):
        return .6 if v[0] else .8

    def handle(self, req):
        path = req.url.path
        body = json.loads(req.content) if req.content else None
        self.requests.append((req.method, path, body))
        if path == '/openapi.json':
            data = dict(paths={p: {m: {}} for p, m in [('/kt/model/info','get'),
                ('/kt/students/{student_id}/state','get'), ('/kt/students/{student_id}/predict','post'),
                ('/kt/rollouts/evaluate','post')]})
        elif path == '/kt/model/info':
            data = dict(model_type='akt_fmkc', model_version=self.model, feature_version=self.feature,
                        kc_feature_names=list(MODEL_FIELDS), limits=self.limits)
        elif path.endswith('/state'):
            data = self.state(path.split('/')[-2])
        elif path.endswith('/predict'):
            student = path.split('/')[-2]
            data = dict(state=self.state(student), predictions=[dict(candidate_id=c['candidate_id'],
                probability=self.probability(c['kc_vector']), attrs=[2 if v is None else 3 for v in c['kc_vector']],
                pid=1, unknown_question=False, unknown_fields=[]) for c in body['candidates']])
        elif path == '/kt/rollouts/evaluate':
            assert not body['retain_branch']
            gain = .04 if body['sequence'][0]['kc_vector'][0] else -.02
            def stats(mean):
                return dict(mean=mean, std=0., q05=mean, q50=mean, q95=mean)
            forecast = {c['candidate_id']: stats(self.probability(c['kc_vector'])+gain) for c in body['forecast_candidates']}
            data = dict(state=self.state(body['student_id']), base_revision=self.revision, branch=None,
                        forecast_statistics=forecast, correct_count=stats(1.), correct_rate=stats(.7))
        else:
            raise AssertionError(f'Unexpected diagnostic call: {req.method} {path}')
        return httpx.Response(200, json=data)


def diagnostic_catalog():
    files, records, proofs = {}, [], {}
    for i, pitches in enumerate(((61, 61, 62), (60, 60, 61), (61, 61, 62))):
        midi = mido.MidiFile()
        for name, values in [('left', [48] * len(pitches)), ('right', pitches)]:
            track = mido.MidiTrack(); midi.tracks.append(track)
            track.append(mido.MetaMessage('track_name', name=name))
            for pitch in values:
                track.append(mido.Message('note_on', note=pitch, velocity=80, time=0))
                track.append(mido.Message('note_off', note=pitch, velocity=0, time=480))
        buffer = io.BytesIO(); midi.save(file=buffer); raw = buffer.getvalue()
        h = sha256(raw).hexdigest()
        sid, seg = str(UUID(int=i+1)), str(UUID(int=i+101))
        records.append(dict(song_id=sid, segment_id=seg, segment_index=1, title=f'Diagnostic {i+1}',
                            midi_sha256=h, storage_path=f'midi/{h}.mid', midi_metadata={}))
        proofs[seg] = dict(midi_sha256=h, context='standalone_reference', kc_labeler_version='1.2.0',
                           evidence='Generated independent three-onset MIDI by pykt_acceptance.diagnostic_catalog')
        files[h] = raw
    def download(req):
        return httpx.Response(200, content=files[req.url.path.split('/')[-1][:-4]])
    with httpx.Client(transport=httpx.MockTransport(download)) as http:
        return CatalogCandidateProvider(http, 'http://diagnostic-storage', context_provenance=proofs).load(records)


def diagnostic_report(client, config, mode):
    student = f'pianokt:{os.environ.get("PIANOKT_ENV", "test")}:diagnostic:phase2'
    before = client._state(student)
    def check(state):
        if state != before or state['history_revision'] != 0 or state['next_position'] != 0:
            raise CompatibilityError('Diagnostic identity must remain empty; never rebuild it')
        return dict(status='diagnostic_cold_start', event_count=0, snapshot_digest=sha256(b'[]').hexdigest(),
                    student_id=student, source='independent diagnostic; no real learner binding')
    result = RecommendationService(client, config).recommend(student, diagnostic_catalog(), check_history=check)
    result.update(execution_mode=mode, catalog_source='generated diagnostic MIDI; mocked public Storage',
                  is_demo=mode == 'offline_mock')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--mock', action='store_true')
    modes.add_argument('--live-diagnostic', action='store_true')
    modes.add_argument('--bound-user', type=UUID)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wide-sweet-spot', action='store_true', help='Diagnostic only: [0,1] instead of default [.6,.8]')
    parser.add_argument('--diagnostic-unknown-threshold', type=float, default=.05)
    args = parser.parse_args()
    config = RecommendationConfig(sweet_min=0 if args.wide_sweet_spot else .6,
                                  sweet_max=1 if args.wide_sweet_spot else .8,
                                  unknown_threshold=args.diagnostic_unknown_threshold)
    if args.mock:
        model = DiagnosticModel()
        with httpx.Client(base_url='http://mock', transport=httpx.MockTransport(model.handle)) as http:
            # Mock contract is never supplied to a live transport.
            client = PyKTClient(http, Compatibility(model.model, model.feature, '1.2.0', 'a'*64))
            report = diagnostic_report(client, config, 'offline_mock')
    elif args.live_diagnostic:
        from .pykt_runtime import verified_client
        with verified_client() as client:
            report = diagnostic_report(client, config, 'live_pykt_diagnostic')
    else:
        if args.wide_sweet_spot or args.diagnostic_unknown_threshold != .05:
            parser.error('--wide-sweet-spot is diagnostic only; configure the bound-user service explicitly')
        from .pykt_runtime import recommend_for_user
        from .online import connect
        from .storage import Objects
        report, _ = recommend_for_user(str(args.bound_user), connect, Objects(os.environ['RAW_ROOT']))
        report['execution_mode'] = 'live_bound_user_readonly'
    report['configuration'] = vars(config) if not args.bound_user else json.loads(os.environ.get('PYKT_RECOMMENDATION_CONFIG', '{}'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('execution_mode','catalog_total','eligible_count','rollout_count','state_unchanged','latency_ms')}))


if __name__ == '__main__':
    main()
