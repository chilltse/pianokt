"""Paired student-cluster summaries and diagnostic exports."""
import csv
import json
import random
from pathlib import Path
import numpy as np
from .benchmark import MU, seed


def diagnostics(runner, state, candidates, student, count):
    before=runner.probe(state)
    differences=before[:,1:]-before[:,:-1]
    output=dict(mu_nonmonotone_fraction=float(((differences.min(1).values < -1e-7)&
        (differences.max(1).values > 1e-7)).float().mean()),
        mu_sensitivity=(before.max(1).values-before.min(1).values).tolist(),
        saturation_fraction=float(((before<.01)|(before>.99)).float().mean()), candidates=[])
    for c in candidates:
        rng=random.Random(seed(runner.seed,student,c['segment_id'],'shuffle'))
        shuffled=list(c['events']); rng.shuffle(shuffled)
        variants=dict(original_order=c['events'], reversed_order=list(reversed(c['events'])), shuffled_order=shuffled)
        values={name:runner.mc(state,dict(c,events=events),student,'diagnostic-'+name,count)
                for name,events in variants.items()}
        for name,y in [('all_correct',1),('all_wrong',0)]:
            values[name]=runner.mc(state,c,student,'diagnostic-'+name,count,forced=y)
        identities={json.dumps(v,separators=(',',':')) for v in c['vectors']}
        overlap=[p['kc_identity'] in identities for p in runner.bank[::5]]
        gains=values['original_order']['kc_gain']
        def group(flag):
            selected=[g for g,o in zip(gains,overlap) if o==flag]
            return sum(selected)/len(selected) if selected else None
        output['candidates'].append(dict(segment_id=c['segment_id'],variants=values,
            probe_overlap_ratio=sum(overlap)/len(overlap),gain_on_exact_overlapping_kcs=group(True),
            gain_on_nonoverlapping_kcs=group(False),history_length=state.next_position,
            cache_length=min(state.next_position,runner.model.seq_len) if runner.model.seq_len else state.next_position,
            rollout_length=len(c['events']),seq_len=runner.model.seq_len))
    return output


def summarize(output, results, seed_value, replicates=2000):
    output=Path(output)
    methods=list(results[0]['selected'])
    def csv_file(name,rows):
        with (output/name).open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    score_rows=[dict(student_checkpoint=r['student_checkpoint'],**s) for r in results for s in r['scores']]
    csv_file('selection_scores.csv',score_rows)
    selected=[dict(student_checkpoint=r['student_checkpoint'],method=m,segment_id=s['segment_id'],
        gain=s['evaluation']['macro_gain'],regret=s['regret'],evaluation_seconds=s['evaluation']['seconds'])
        for r in results for m,s in r['selected'].items()]
    csv_file('selected_segments.csv',selected)
    paired=[]
    rng=np.random.default_rng(seed_value)
    for other in ['one_step_gain','sweet_spot','random']:
        clusters={}
        for r in results:
            delta=r['selected']['full_segment_mc']['evaluation']['macro_gain']-r['selected'][other]['evaluation']['macro_gain']
            clusters.setdefault(r['student_cluster'],[]).append(delta)
        values=np.array([np.mean(v) for v in clusters.values()])
        boots=np.array([rng.choice(values,len(values),replace=True).mean() for _ in range(replicates)])
        paired.append(dict(comparison='full_segment_mc vs '+other,mean_difference=float(values.mean()),
            ci_low=float(np.quantile(boots,.025)) if len(values)>1 else None,
            ci_high=float(np.quantile(boots,.975)) if len(values)>1 else None,
            number_of_students=len(values),number_of_checkpoints=len(results),
            inference='synthetic_only' if results[0].get('mock') else 'model_internal_only'))
    csv_file('paired_comparisons.csv',paired)
    metrics=[]
    for m in methods:
        gains=[r['selected'][m]['evaluation']['macro_gain'] for r in results]
        times=[r['selected'][m]['evaluation']['seconds'] for r in results]
        regrets=[r['selected'][m]['regret'] for r in results]
        metrics.append(dict(method=m,mean_gain=float(np.mean(gains)),median_gain=float(np.median(gains)),
            gain_std=float(np.std(gains)),mean_regret=float(np.mean(regrets)),median_regret=float(np.median(regrets)),
            runtime_p50=float(np.quantile(times,.5)),runtime_p95=float(np.quantile(times,.95)),
            runtime_scope='independent_evaluation_only'))
    csv_file('evaluation_metrics.csv',metrics)
    csv_file('reference_regret.csv',[dict(method=m,epsilon=e,epsilon_optimal_rate=float(np.mean(
        [r['selected'][m]['regret']<=e for r in results]))) for m in methods for e in [.001,.005,.01]])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    figures=output/'figures';figures.mkdir(exist_ok=True)
    def save(name,title):
        plt.title(title+' (model simulation)');plt.tight_layout();plt.savefig(figures/(name+'.png'));plt.close()
    plt.bar(methods,[m['mean_gain'] for m in metrics]);plt.xticks(rotation=20);save('method_gain','Mean macro gain')
    for m in methods:
        selected=[r['selected'][m]['evaluation'] for r in results]
        plt.plot(MU,np.mean([e['mean_p_after'] for e in selected],axis=0),label=m)
    plt.plot(MU,results[0]['selected'][methods[0]]['evaluation']['p_before'],'--',label='before (first snapshot)')
    plt.legend();save('before_after','Five-level predictions')
    for m in methods:
        plt.plot(MU,np.mean([r['selected'][m]['evaluation']['gain'] for r in results],axis=0),label=m)
    plt.legend();save('mu_gain','Five-level gain')
    before=results[0]['selected'][methods[0]]['evaluation']['before_predictions']
    for row in before:plt.plot(MU,row,alpha=.3)
    save('mu_response','KC response curves, first snapshot')
    plt.scatter([m['runtime_p50'] for m in metrics],[m['mean_gain'] for m in metrics]);plt.xlabel('Evaluation seconds');save('runtime_gain','Evaluation cost and gain')


def budget_sweep(runner,state,candidates,student,reference,budgets=(4,8,16,32,64,128),repeats=3):
    from scipy.stats import spearmanr
    ref=np.array([r['macro_gain'] for r in reference]); rows=[]
    for n in budgets:
        choices=[]
        for repeat in range(repeats):
            estimates=[runner.mc(state,c,student,'budget-'+str(repeat),n) for c in candidates]
            values=np.array([r['macro_gain'] for r in estimates]); chosen=int(values.argmax());choices.append(chosen)
            correlation=float(spearmanr(values,ref)[0]) if len(ref)>1 and np.std(ref)>0 and np.std(values)>0 else None
            rows.append(dict(mc_budget=n,repeat=repeat,segment_id=candidates[chosen]['segment_id'],
                regret=float(ref.max()-ref[chosen]),ranking_spearman=correlation,
                seconds=sum(r['seconds'] for r in estimates)))
        stability=max(choices.count(c) for c in set(choices))/repeats
        for row in rows[-repeats:]:row['top1_selection_stability']=stability
    return rows
