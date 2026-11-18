"""Development-only expected-gain recommendations on a fixed target distribution."""
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
import random
from time import perf_counter

from .pykt_integration import CompatibilityError, canonical, check_rollout_limits


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class RecommendationConfig:
    sweet_min: float = .60
    sweet_max: float = .80
    unknown_threshold: float = .05
    shortlist: int = 10
    target_count: int = 64
    trajectories: int = 8
    seed: int = 7
    top_k: int = 5

    def __post_init__(self):
        if not 0 <= self.sweet_min <= self.sweet_max <= 1 or not 0 <= self.unknown_threshold <= 1:
            raise ValueError('Invalid Sweet Spot or unknown threshold')
        for name, upper in [('shortlist', 10), ('target_count', 64), ('trajectories', 1024), ('top_k', 5)]:
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= upper:
                raise ValueError(f'Invalid {name}')
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError('Invalid seed')


def fixed_targets(rows, maximum, seed):
    """Empirical onset-frequency distribution; seeded sampling only when > maximum KCs.

    Small libraries retain exact frequencies. Large libraries use multinomial samples
    with replacement; repeated draws become weights, so frequent KCs remain frequent.
    """
    counts = Counter(canonical(r['kc_vector']) for r in rows)
    if not counts:
        return []
    keys = sorted(counts)
    if len(keys) > maximum:
        draws = random.Random(seed).choices(keys, weights=[counts[k] for k in keys], k=maximum)
        counts = Counter(draws)
    examples = {canonical(r['kc_vector']): r for r in rows}
    total = sum(counts.values())
    return [dict(candidate_id='target-' + fingerprint(key), kc_vector=examples[key]['kc_vector'],
                 weight=counts[key]/total, row=examples[key]) for key in sorted(counts)]


class RecommendationService:
    def __init__(self, client, config=None):
        self.client = client
        self.config = config or RecommendationConfig()

    def recommend(self, student_id, candidates, *, check_history):
        start = perf_counter()
        c, cfg = self.client, self.config
        info = c.inspect()
        before = c._state(student_id)
        provenance = check_history(before)
        calls = c.prediction_calls
        eligible = [item for item in candidates if item['status'] == 'eligible' and item['rows']]
        rows = [r for item in eligible for r in item['rows']]
        def same(result):
            if result['state'] != before:
                raise CompatibilityError('Recommendation state changed; discard entire round')
        prediction = c.predict(student_id, rows)
        same(prediction)
        offset = 0
        accepted = []
        diagnostics = [{k: v for k, v in item.items() if k != 'rows'} for item in candidates]
        by_identity = {(d['song_id'], d['segment_id']): d for d in diagnostics}
        for item in eligible:
            n = len(item['rows'])
            ps = prediction['onsets'][offset:offset+n]; offset += n
            d = by_identity[(item['song_id'], item['segment_id'])]
            d.update(predicted_accuracy=sum(p['probability'] for p in ps)/n,
                     unknown_question_ratio=sum(p['unknown_question'] for p in ps)/n,
                     unknown_field_ratio=sum(len(p['unknown_fields']) for p in ps)/(6*n))
            if max(d['unknown_question_ratio'], d['unknown_field_ratio']) > cfg.unknown_threshold:
                d.update(status='skipped', reason='unknown_ratio_exceeded')
            elif not cfg.sweet_min - 1e-12 <= d['predicted_accuracy'] <= cfg.sweet_max + 1e-12:
                d.update(status='skipped', reason='outside_sweet_spot')
            else:
                accepted.append((item, d))
        sweet_count = len(accepted)
        # All eligible reference onsets define T, independent of each candidate's outcome.
        targets = fixed_targets(rows, cfg.target_count, cfg.seed)
        target_spec = [{k: v for k, v in t.items() if k != 'row'} for t in targets]
        target_digest = fingerprint(dict(seed=cfg.seed, targets=target_spec))
        forecast = [{k: t[k] for k in ('candidate_id', 'kc_vector')} for t in targets]
        supported = []
        for item, d in accepted:
            try:
                check_rollout_limits(info['limits'], len(item['rows']), cfg.trajectories, len(targets), False)
            except ValueError as exc:
                if isinstance(exc, CompatibilityError):
                    raise
                d.update(status='skipped', reason='full_song_rollout_limit')
            else:
                supported.append((item, d))
        center = (cfg.sweet_min + cfg.sweet_max)/2
        supported.sort(key=lambda pair: (abs(pair[1]['predicted_accuracy']-center),
                                         pair[0]['song_id'], pair[0]['segment_id']))
        shortlist = supported[:cfg.shortlist]
        for _, d in supported[cfg.shortlist:]:
            d.update(status='skipped', reason='shortlist_limit')
        results = []
        if shortlist:
            baseline = c.predict(student_id, [t['row'] for t in targets]); same(baseline)
            p_before = {t['candidate_id']: p['probability'] for t, p in zip(targets, baseline['onsets'], strict=True)}
            for item, d in shortlist:
                rollout = c.rollout(student_id, item['rows'], seed=cfg.seed, num_trajectories=cfg.trajectories,
                                    forecast_candidates=forecast, include_steps=False)
                same(rollout)
                stats = rollout.get('forecast_statistics', {})
                if set(stats) != set(p_before):
                    raise CompatibilityError('Forecast target IDs differ from fixed distribution')
                for s in stats.values():
                    if type(s.get('mean')) not in (int, float) or not isfinite(s['mean']) or not 0 <= s['mean'] <= 1:
                        raise CompatibilityError('Invalid forecast probability')
                gain = sum(t['weight'] * (stats[t['candidate_id']]['mean'] - p_before[t['candidate_id']]) for t in targets)
                d.update(status='ranked', predicted_learning_gain=gain)
                results.append(dict(song_id=item['song_id'], segment_id=item['segment_id'],
                    segment_index=item['segment_index'], title=item['title'], song_version=item['song_version'],
                    predicted_accuracy=d['predicted_accuracy'], predicted_learning_gain=gain, score=gain,
                    unknown_question_ratio=d['unknown_question_ratio'], unknown_field_ratio=d['unknown_field_ratio'],
                    rollout_statistics=dict(correct_rate=rollout['correct_rate'], correct_count=rollout['correct_count'],
                                            forecast_statistics=stats, trajectories=cfg.trajectories, seed=cfg.seed),
                    reason='模型隔离模拟在固定目标 KC 分布上的预期收益；不代表真实教学效果'))
        else:
            p_before = {}
        if canonical(check_history(before)) != canonical(provenance):
            raise CompatibilityError('READY history binding changed during recommendation')
        # The source recheck may perform slow I/O; recheck the remote state after it.
        c.inspect()
        if c._state(student_id) != before:
            raise CompatibilityError('Recommendation state changed; discard entire round')
        results.sort(key=lambda x: (-x['predicted_learning_gain'], x['song_id'], x['segment_id']))
        top = [dict(x, rank=i+1) for i, x in enumerate(results[:cfg.top_k])]
        return dict(model_version=before['model_version'], feature_version=before['feature_version'],
            kc_labeler_version=c.compatibility.kc_labeler_version, evidence_sha256=c.compatibility.evidence_sha256,
            student_id=student_id, history_revision=before['history_revision'], history_sync_provenance=provenance,
            cold_start=provenance['event_count'] == 0, is_demo=False, items=top, top5=top,
            catalog_total=len(candidates), eligible_count=len(eligible),
            skipped=[d for d in diagnostics if d['status'] == 'skipped'], candidates=diagnostics,
            unique_kc_count=len({canonical(r['kc_vector']) for r in rows}),
            prediction_calls=c.prediction_calls-calls, sweet_spot_count=sweet_count,
            rollout_count=len(shortlist), target_set_digest=target_digest, targets=target_spec,
            target_seed=cfg.seed, p_before=p_before, latency_ms=round((perf_counter()-start)*1000, 2), state_unchanged=True)
