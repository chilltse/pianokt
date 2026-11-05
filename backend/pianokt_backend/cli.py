import argparse
import json
import os
import uuid
from pathlib import Path
from contextlib import nullcontext
from .storage import Objects

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('align')
    a.add_argument('--reference',required=True,type=Path); a.add_argument('--performance',required=True,type=Path)
    a.add_argument('--attempt-id'); a.add_argument('--song-id',default='local-example')
    a.add_argument('--raw',default='data/raw'); a.add_argument('--lake',default='data/lakehouse')
    a=sub.add_parser('pipeline'); a.add_argument('--replay',action='store_true')
    a=sub.add_parser('relay'); a.add_argument('--limit',type=int,default=1000)
    a=sub.add_parser('worker'); a.add_argument('attempt_id')
    sub.add_parser('spark-events')
    a=sub.add_parser('export-training'); a.add_argument('--cutoff',required=True); a.add_argument('--output-key',required=True); a.add_argument('--run-selection',type=Path)
    a=sub.add_parser('export-attempts'); a.add_argument('--cutoff',required=True); a.add_argument('--output-prefix',required=True); a.add_argument('--run-selection',type=Path)
    for name in ('export-attempts','export-training'):
        sub.choices[name].add_argument('--source',choices=('lake','direct'),default='direct')
        sub.choices[name].add_argument('--environment', choices=('production','development','test','staging'), default='production')
    a=sub.add_parser('rebuild-projections'); a.add_argument('--attempt-id', required=True, type=uuid.UUID)
    args=p.parse_args()
    if args.command=='rebuild-projections':
        from .online import connect
        from .projections import rebuild_projections
        with connect() as c: rebuild_projections(c,args.attempt_id)
        print(json.dumps({'rebuilt':str(args.attempt_id)})); return
    run_selection=json.loads(args.run_selection.read_text()) if getattr(args,'run_selection',None) else None
    if run_selection is not None and (not isinstance(run_selection,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in run_selection.items())): p.error('run-selection must map attempt IDs to run IDs')
    if args.command=='export-attempts':
        from .pipeline.attempts import export_attempts,read_context,utc
        from .online import connect
        with connect() as c:
            c.execute('set transaction isolation level repeatable read read only')
            context=read_context(c,utc(args.cutoff),args.environment)
            if args.source=='direct':
                from .pipeline.direct import read_committed
                source=read_committed(c,utc(args.cutoff),args.environment)
        if args.source=='lake':
            from .pipeline.tables import Tables
            source=Tables(os.getenv('LAKE_ROOT','data/lakehouse'))
        print(json.dumps(export_attempts(Objects(os.getenv('RAW_ROOT','data/raw')),source,args.cutoff,args.output_prefix,context,run_selection,args.environment))); return
    if args.command=='export-training':
        from .pipeline.training import export_dataset
        from .pipeline.attempts import read_context,utc
        from .online import connect
        with connect() as c:
            c.execute('set transaction isolation level repeatable read read only')
            context=read_context(c,utc(args.cutoff),args.environment)
            if args.source=='direct':
                from .pipeline.direct import read_committed
                source=read_committed(c,utc(args.cutoff),args.environment)
        if args.source=='lake':
            from .pipeline.tables import Tables
            source=Tables(os.getenv('LAKE_ROOT','data/lakehouse'))
        print(export_dataset(Objects(os.getenv('RAW_ROOT','data/raw')),source,args.cutoff,args.output_key,context,run_selection,args.environment)); return
    if args.command=='relay':
        from .worker import relay
        print(relay(args.limit)); return
    if args.command=='worker':
        from .worker import process
        process(args.attempt_id); return
    if args.command=='spark-events':
        from .pipeline.spark_events import run
        run(os.environ['RAW_ROOT'],os.environ['LAKE_ROOT'],os.environ['CHECKPOINT_ROOT']); return
    from .pipeline.tables import Tables
    from .pipeline.jobs import catch_up
    store=Objects(getattr(args,'raw',os.getenv('RAW_ROOT','data/raw')))
    tables=Tables(getattr(args,'lake',os.getenv('LAKE_ROOT','data/lakehouse')))
    if args.command=='align':
        from .alignment.service import align
        attempt=args.attempt_id or str(uuid.uuid4())
        for kind,path in [('reference',args.reference),('performance',args.performance)]: store.put(f'midi/{attempt}/{kind}.mid',path.read_bytes())
        result=align(args.reference,args.performance,attempt,stage_sink=lambda run,data:store.json(f'matching/{run}/matches.json',data))
        from .provenance import recording_environment
        result.update(learner_key='synthetic-local',song_id=args.song_id,source_environment=recording_environment())
        store.json(f"alignment/{result['alignment_run_id']}/result.json",result)
    from .online import connect
    with (connect() if os.getenv('DATABASE_URL') else nullcontext(None)) as lock:
        if lock and not lock.execute("select pg_try_advisory_lock(hashtext('pianokt-lakehouse-v1')) as acquired").fetchone()['acquired']: raise RuntimeError('Pipeline writer already running')
        try:
            if args.command=='pipeline' and os.getenv('ENABLE_SPARK_EVENTS')=='1':
                from .pipeline.spark_events import run
                run(os.environ['RAW_ROOT'],os.environ['LAKE_ROOT'],os.environ['CHECKPOINT_ROOT'])
            print(json.dumps({'published_runs':catch_up(store,tables,getattr(args,'replay',False))}))
        finally:
            if lock: lock.execute("select pg_advisory_unlock(hashtext('pianokt-lakehouse-v1'))")

if __name__=='__main__': main()
