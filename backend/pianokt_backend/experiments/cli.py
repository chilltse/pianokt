"""Run with PYTHONPATH=<pykt>:backend python -m pianokt_backend.experiments.cli."""
import argparse
import csv
import json
from pathlib import Path
import torch
import yaml
from .benchmark import Benchmark, MU, bayesian_control, build_bank, digest
from .analysis import diagnostics, summarize, budget_sweep


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--config')
    p.add_argument('--mode', choices=['mock', 'offline', 'dev'], default='mock')
    p.add_argument('--output', required=True)
    p.add_argument('--dry-run', action='store_true')
    for name, default in [('student-limit',100),('candidate-limit',100),('selection-mc',32),
                          ('evaluation-mc',256),('reference-mc',512),('seed',3407)]:
        p.add_argument('--'+name, type=int, default=default)
    args = p.parse_args()
    config = yaml.safe_load(Path(args.config).read_text()) if args.config else {}
    if args.mode == 'dev':
        raise ValueError('Export verified development snapshots first; use offline mode. Direct database access is not implemented.')
    torch.set_num_threads(1)
    torch.manual_seed(args.seed)
    from pykt.models.akt_fmkc import AKTFMKC
    from pykt.models.akt_fmkc_runner import load_fmkc_checkpoint
    from pykt.serving.encoder import KCEncoder
    if args.mode == 'mock':
        model = AKTFMKC([5]*6, 2, d_model=8, n_blocks=1, dropout=0., field_dim=2,
                         num_attn_heads=2, d_ff=16, final_fc_dim=16, drift='response', seq_len=8).eval()
        with torch.no_grad():
            model.question_scalar.weight.normal_()
        vectors = [[False,1,False,30,0,0], [True,2,True,50,1,1]]
        from pykt.preprocess.vector_kc_preprocess import category
        tokens = [[category(x) for x in v] for v in vectors]
        encoding = dict(field_names=['kc_'+str(i) for i in range(6)],
            fields=[{tokens[j][i]:3+j for j in range(2)} for i in range(6)],
            questions={json.dumps(t,separators=(',',':')):i+1 for i,t in enumerate(tokens)})
        checkpoint_hash = 'synthetic_untrained_model'
        data = dict(candidates=[dict(segment_id='mock-'+str(i), duration_seconds=2.,
            vectors=[v, vectors[1-i]]) for i,v in enumerate(vectors)],
            students=[dict(student_id='mock-student', checkpoint_id='mock-0', history=[])])
    else:
        checkpoint = Path(config['checkpoint'])
        model, meta = load_fmkc_checkpoint(checkpoint)
        checkpoint_hash = __import__('hashlib').sha256(checkpoint.read_bytes()).hexdigest()
        encoding = meta['data_config']['encoding']
        if config.get('local_feature_root'):
            from .local_features import load_local_features
            data=load_local_features(config['local_feature_root'],config['student_history'])
        else:
            data = json.loads(Path(config['snapshot']).read_text())
            if data.get('source_environment') not in ('development','test') or data.get('verified') is not True:
                raise ValueError('Require verified development/test snapshot')
            if data.get('checkpoint_sha256') != checkpoint_hash or data.get('kc_labeler_version') != '1.2.0':
                raise ValueError('Snapshot checkpoint/labeler mismatch')
            # Re-extract trusted local reference MIDI; never trust supplied encoded candidate IDs.
            from pianokt_backend.pykt_integration import ReferenceCandidates, validate_vector
            extractor = ReferenceCandidates()
            for c in data['candidates']:
                proof = c['provenance']
                raw_hash = __import__('hashlib').sha256(Path(c['midi_path']).read_bytes()).hexdigest()
                if proof.get('context') != 'standalone_reference' or not proof.get('evidence') or proof.get('midi_sha256') != raw_hash or proof.get('kc_labeler_version') != '1.2.0':
                    raise ValueError('Missing hash-bound standalone provenance')
                rows = extractor.extract(Path(c['midi_path']), song_id=c['song_id'],
                    song_version=c['song_version'], segment_id=c['segment_id'])
                c['vectors'] = [r['kc_vector'] for r in rows]
                for v in c['vectors']: validate_vector(v)
    encoder = KCEncoder(encoding, [e.num_embeddings for e in model.field_embeddings], model.n_pid)
    bank, coverage = build_bank([v for c in data['candidates'] for v in c['vectors']], encoder)
    candidates = []
    for c in sorted(data['candidates'], key=lambda c:(len(c['vectors']),c['segment_id']) if config.get('candidate_order')=='shortest_first' else (0,c['segment_id'])):
        encoded = [encoder.encode(v) for v in c['vectors']]
        if encoded and all(q.pid>0 and not q.unknown_fields for q in encoded):
            candidates.append(dict(c, events=[dict(attrs=q.attrs,pid=q.pid) for q in encoded]))
    candidates=candidates[:args.candidate_limit]
    if not candidates: raise ValueError('No fully known candidates')
    versions = dict(model_version=checkpoint_hash, feature_version=encoder.version)
    runner = Benchmark(model, bank, versions, args.seed)
    output = Path(args.output)
    output.mkdir(parents=True,exist_ok=True)
    import subprocess
    def commit(path):
        return subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
    manifest = dict(pianokt_commit=commit(Path(__file__).parents[3]),pykt_commit=commit(Path(__import__('pykt').__file__).parents[1]),mode=args.mode, checkpoint_sha256=checkpoint_hash, feature_version=encoder.version,
        probe_bank_digest=digest(bank), catalog_digest=digest(candidates), student_snapshot_digest=digest(data['students']),
        implementation_digest=digest({p.name:p.read_text() for p in Path(__file__).parent.glob('*.py')}),
        data_provenance={k:data.get(k) for k in ('history_origin','history_warning','history_sha256','catalog_manifest_sha256','ready_status_checked','standalone_reference_sidecar_checked')},
        runtime_configuration=vars(args), kc_labeler_version='1.2.0', drift=model.drift,
        future_validation='not_evaluated_insufficient_real_outcomes')
    def write(name, obj):
        (output/name).write_text(json.dumps(obj,indent=2,allow_nan=False))
    if (output/'manifest.json').exists() and json.loads((output/'manifest.json').read_text()) != manifest:
        raise ValueError('Output belongs to another run/configuration')
    write('manifest.json',manifest)
    write('probe_bank.json',bank)
    write('kc_coverage.json',coverage)
    (output/'config_resolved.yaml').write_text(yaml.safe_dump(dict(config,cli=vars(args))))
    if model.drift != 'none':
        values=model.question_scalar.weight[1:].detach().flatten()
        write('mu_distribution.json',dict(min=float(values.min()),max=float(values.max()),mean=float(values.mean()),
            std=float(values.std()), levels_in_range={str(m):bool(values.min()<=m<=values.max()) for m in MU}))
    if args.dry_run: return
    results=[]
    for student in data['students'][:args.student_limit]:
        print('Replaying student',len(results)+1, 'events',len(student['history']),flush=True)
        identity=digest([student['student_id'],student['checkpoint_id']])[:20]
        cache=model.empty_cache(**versions)
        for i,e in enumerate(student['history']):
            q=encoder.encode(e['kc_vector'])
            cache,_=model.observe(cache,q.attrs,q.pid,e['response'],'history:'+str(i),**versions)
        print('Replay complete; validating probes',flush=True)
        # Formal gate on the actual checkpoint and all bank questions.
        for probe in bank[::5]:
            mu=model.question_scalar.weight[probe['pid']].item() if model.drift!='none' else 0.
            expected=model.predict_next(cache,probe['attrs'],probe['pid'],**versions)
            actual=model.predict_next_with_mu_override(cache,probe['attrs'],probe['pid'],mu,**versions)
            torch.testing.assert_close(actual,expected,atol=1e-6,rtol=0.)
        batched=runner.probe(cache)
        scalar=torch.tensor([float(model.predict_next_with_mu_override(cache,p['attrs'],p['pid'],p['mu_override'],**versions)) for p in bank]).reshape(-1,5)
        torch.testing.assert_close(batched,scalar,atol=2e-5,rtol=0.)
        batch_max_error=float((batched-scalar).abs().max())
        print('Probe parity passed; running estimators',flush=True)
        result_path=output/(identity+'.json')
        if result_path.exists(): result=json.loads(result_path.read_text())
        else:
            result=runner.run(cache,candidates,identity,args.selection_mc,args.evaluation_mc,args.reference_mc)
            result['probe_batch_max_abs_error']=batch_max_error
            result['probe_batch_atol']=2e-5
            result['student_cluster']=digest(student['student_id'])[:20]
            result['mock']=args.mode=='mock'
            result['budget_sweep']=budget_sweep(runner,cache,candidates,identity,result['reference'],config.get('mc_budgets',[4,8,16,32,64,128]),config.get('budget_repeats',3))
            result['diagnostics']=diagnostics(runner,cache,candidates,identity,args.selection_mc)
            write(result_path.name,result)
        results.append(result)
    write('results.json',results)
    summarize(output,results,args.seed)
    write('diagnostics.json',dict(static_bayesian_control=bayesian_control(),
        noop_max_error=max(r['noop_max_error'] for r in results)))
    rows=[]
    for r in results:
        for method,s in r['selected'].items():
            for i,mu in enumerate(MU):
                e=s['evaluation']
                rows.append(dict(student_checkpoint=r['student_checkpoint'],method=method,segment_id=s['segment_id'],
                    mu_level=mu,p_before=e['p_before'][i],p_after_mean=e['mean_p_after'][i],gain=e['gain'][i],
                    gain_std=e['gain_std'][i],gain_sem=e['gain_sem'][i],n_rollouts=e['n_rollouts']))
    with (output/'probe_gain_by_mu.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    (output/'report.md').write_text('# Offline benchmark\n\nMode: '+args.mode+'\n\n'+
        'Frozen-model counterfactual predictions; not evidence of human learning.\n\n'+
        f"Known KC: {coverage['known_kc']}; probes: {len(bank)}; checkpoints: {len(results)}.\n\n"+
        'Independent selection/evaluation/reference RNG streams. Reference is restricted to configured candidates.\n\n'+
        'Not yet implemented: adaptive reference, history-length sensitivity, full figure set, direct dev adapter.\n')
    print(json.dumps(dict(output=str(output),coverage=coverage,checkpoints=len(results))))

if __name__ == '__main__': main()
