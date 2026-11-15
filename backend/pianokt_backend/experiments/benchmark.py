"""Offline frozen-model estimators. No HTTP mutations or persistent state stores."""
import hashlib
import json
import math
import random
import time
import torch

MU = (-.5, -.25, 0., .25, .5)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def seed(*parts):
    return int(digest(parts)[:16], 16)


def build_bank(vectors, encoder):
    unique = {json.dumps(v, separators=(',', ':'), allow_nan=False): v for v in vectors}
    bank = []
    for identity, vector in sorted(unique.items()):
        q = encoder.encode(vector)
        if q.pid == 0 or q.unknown_fields:
            continue
        for mu in MU:
            bank.append(dict(probe_id=digest([identity, mu]), kc_identity=identity,
                             kc_vector=vector, attrs=q.attrs, pid=q.pid, mu_override=mu))
    if not bank:
        raise ValueError('No known catalog KC vectors; cannot run main experiment')
    return bank, dict(distinct_kc=len(unique), known_kc=len(bank)//5,
                      probe_count=len(bank), known_coverage=len(bank)/5/len(unique))


class Benchmark:
    def __init__(self, model, bank, versions, experiment_seed=3407):
        self.model, self.bank, self.versions = model.eval(), bank, versions
        self.seed = experiment_seed
        self.probe_calls = self.simulated_onsets = 0
        self.model.requires_grad_(False)

    def predict(self, state, event):
        return float(self.model.predict_next(state, event['attrs'], event['pid'], **self.versions))

    def observe(self, state, event, response):
        self.simulated_onsets += 1
        return self.model.observe(state, event['attrs'], event['pid'], response,
                                  'experiment:'+str(state.next_position), **self.versions)[0]

    @torch.inference_mode()
    def probe(self, state):
        self.probe_calls += len(self.bank)
        if not hasattr(self.model, '_question_step'):
            return torch.tensor([float(self.model.predict_next_with_mu_override(
                state,p['attrs'],p['pid'],p['mu_override'],**self.versions)) for p in self.bank]).reshape(-1,5)
        self.model.validate_cache(state, **self.versions)
        if not hasattr(self, '_encoded_probes'):
            device=next(self.model.parameters()).device
            levels=[]
            for j,mu in enumerate(MU):
                entries=self.bank[j::5]
                attrs=torch.tensor([p['attrs'] for p in entries],device=device).unsqueeze(1)
                pid=torch.tensor([p['pid'] for p in entries],device=device).unsqueeze(1)
                levels.append(self.model.encode_question(attrs,pid,mu_override=mu)[0])
            self._encoded_probes=torch.stack(levels,dim=1).reshape(-1,1,levels[0].shape[-1])
        return torch.cat([self.model._question_step(state,q)[0].reshape(-1)
                          for q in self._encoded_probes.split(64)]).cpu().reshape(-1,5)

    def trajectory(self, state, events, rng, forced=None):
        for event in events:
            p = self.predict(state, event)
            state = self.observe(state, event, int(rng.random() < p) if forced is None else forced)
        return state

    def mc(self, state, candidate, student, phase, count, forced=None):
        if count < 2:
            raise ValueError('MC requires at least two trajectories')
        before = self.probe(state)
        started = time.perf_counter()
        after = torch.stack([self.probe(self.trajectory(state, candidate['events'],
            random.Random(seed(self.seed, student, candidate['segment_id'], phase, i)), forced))
            for i in range(count)])
        gains = (after-before).mean(1)
        macro = gains.mean(1)
        return dict(p_before=before.mean(0).tolist(), mean_p_after=after.mean((0, 1)).tolist(),
                    gain=gains.mean(0).tolist(), gain_std=gains.std(0).tolist(),
                    gain_sem=(gains.std(0)/math.sqrt(count)).tolist(),
                    macro_gain=float(macro.mean()), macro_sem=float(macro.std()/math.sqrt(count)),
                    n_rollouts=count, seconds=time.perf_counter()-started,
                    kc_gain=(after.mean(0)-before).mean(1).tolist(),
                    before_predictions=before.tolist(), after_predictions=after.mean(0).tolist())

    def one_step(self, state, candidate):
        baseline = float(self.probe(state).mean())
        gains = []
        for event in candidate['events']:
            p = self.predict(state, event)
            values = [float(self.probe(self.observe(state, event, y)).mean()) for y in (0, 1)]
            gains.append(p*values[1]+(1-p)*values[0]-baseline)
        return dict(mean=sum(gains)/len(gains), sum=sum(gains),
                    per_second=sum(gains)/candidate['duration_seconds'])

    def run(self, state, candidates, student, selection_mc=32, evaluation_mc=256, reference_mc=512):
        if not candidates or any(not c['events'] or c['duration_seconds'] <= 0 for c in candidates):
            raise ValueError('Nonempty candidates and positive durations required')
        scores = []
        for c in candidates:
            probabilities = [self.predict(state, e) for e in c['events']]
            one = self.one_step(state, c)
            mc = self.mc(state, c, student, 'selection', selection_mc)
            scores.append(dict(segment_id=c['segment_id'], sweet_spot=-abs(sum(probabilities)/len(probabilities)-.7),
                percentage_p_above_0_5=sum(p>.5 for p in probabilities)/len(probabilities),
                one_step_gain=one['mean'], one_step_sum=one['sum'], one_step_per_second=one['per_second'],
                full_segment_mc=mc['macro_gain'], gain_per_second=mc['macro_gain']/c['duration_seconds'],
                gain_per_onset=mc['macro_gain']/len(c['events'])))
        indices = {m: max(range(len(scores)), key=lambda i: scores[i][m])
                   for m in ('sweet_spot', 'one_step_gain', 'full_segment_mc')}
        indices['random'] = random.Random(seed(self.seed, student, 'random')).randrange(len(candidates))
        evaluation = {i:self.mc(state, candidates[i], student, 'evaluation', evaluation_mc) for i in set(indices.values())}
        reference = [self.mc(state, c, student, 'reference', reference_mc) for c in candidates]
        best = max(r['macro_gain'] for r in reference)
        selected = {m:dict(segment_id=candidates[i]['segment_id'], evaluation=evaluation[i],
                    regret=best-reference[i]['macro_gain']) for m,i in indices.items()}
        noop = float((self.probe(state)-self.probe(state)).abs().max())
        return dict(student_checkpoint=student, scores=scores, selected=selected, reference=reference,
                    noop_max_error=noop, history_length=state.next_position, seq_len=self.model.seq_len)


def bayesian_control(count=10000, length=20, experiment_seed=3407):
    rng = random.Random(experiment_seed)
    gains = []
    for _ in range(count):
        a = b = 1
        for _ in range(length):
            y = rng.random() < a/(a+b)
            a += y
            b += not y
        gains.append(a/(a+b)-.5)
    mean = sum(gains)/count
    sem = math.sqrt(sum((g-mean)**2 for g in gains)/(count-1)/count)
    return dict(mean_gain=mean, sem=sem, n=count, exact_expected_gain=0.)
