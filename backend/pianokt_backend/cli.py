import argparse
import json
import logging
import os
import uuid
from pathlib import Path
from .alignment.alignment_policies import DEFAULT_ALIGNMENT_VERSION

from .storage import Objects

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('align')
    a.add_argument('--reference',required=True,type=Path); a.add_argument('--performance',required=True,type=Path)
    a.add_argument('--attempt-id'); a.add_argument('--song-id',default='local-example')
    a.add_argument('--output',type=Path,default=Path('data/debug/result.json'))
    a=sub.add_parser('extract-reference', help='Extract reference-only MIDI/KC features locally')
    a.add_argument('input', type=Path, help='MIDI file or directory (recursive)')
    a.add_argument('--output', required=True, type=Path, help='JSON file, or output directory for directory input')
    a.add_argument('--kc-labeler-version', default=None)
    a.add_argument('--reference-grouping-version', default=None)
    a=sub.add_parser('relay'); a.add_argument('--limit',type=int,default=1000)
    a.add_argument('--max-seconds',type=float,default=600)
    a=sub.add_parser('worker'); a.add_argument('attempt_id')
    a=sub.add_parser('export-training'); a.add_argument('--cutoff',required=True); a.add_argument('--output-prefix'); a.add_argument('--run-selection',type=Path)
    a=sub.add_parser('export-attempts'); a.add_argument('--cutoff',required=True); a.add_argument('--output-prefix'); a.add_argument('--run-selection',type=Path)
    for name in ('export-attempts','export-training'):
        sub.choices[name].add_argument('--kc-labeler-version')
        sub.choices[name].add_argument('--alignment-version')
        sub.choices[name].add_argument('--reference-grouping-version')
        sub.choices[name].add_argument('--environment', choices=('production','development','test','staging'), default='production')
    a=sub.add_parser('reprocess', help='Explicit research-only processing; no online promotion')
    a.add_argument('--attempt-id', action='append', required=True, type=uuid.UUID)
    a.add_argument('--environment', required=True, choices=('production','development','test','staging'))
    a.add_argument('--alignment-version', default=DEFAULT_ALIGNMENT_VERSION)
    a.add_argument('--reference-grouping-version', default='1.0.0')
    a.add_argument('--kc-labeler-version')
    a.add_argument('--dry-run', action='store_true')
    a=sub.add_parser('rebuild-projections'); a.add_argument('--attempt-id', required=True, type=uuid.UUID)
    from .song_import import add_import_parser
    add_import_parser(sub)
    from .catalog_sync import add_sync_parser
    add_sync_parser(sub)
    args=p.parse_args()
    if args.command=='reprocess':
        from .reprocess import reprocess
        report = reprocess(Objects(os.environ['DATA_ROOT']), args.attempt_id, args.environment,
                           alignment_version=args.alignment_version, reference_grouping_version=args.reference_grouping_version,
                           kc_labeler_version=args.kc_labeler_version, dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False))
        if report['failed']: raise SystemExit(1)
        return
    if args.command=='sync-catalog':
        from .catalog_sync import run_sync
        print(json.dumps(run_sync(args),ensure_ascii=False,indent=2))
        return
    if args.command=='extract-reference':
        from .alignment.feature_extraction.reference import extract_midi_reference
        from .alignment.feature_extraction import resolve_kc_version
        resolve_kc_version(args.kc_labeler_version)
        directory = args.input.is_dir()
        paths = sorted(x for x in args.input.rglob('*') if x.is_file() and x.suffix.lower() in ('.mid','.midi')) if directory else [args.input]
        if not paths:
            p.error('No MIDI files found')
        failed = False
        for path in paths:
            target = args.output / path.relative_to(args.input).parent / (path.name + '.json') if directory else args.output
            try:
                result = extract_midi_reference(path, kc_labeler_version=args.kc_labeler_version, reference_grouping_version=args.reference_grouping_version)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
                print(json.dumps({'input':str(path),'output':str(target),'events':result['reference_events_count']}, ensure_ascii=False))
            except Exception as exc:
                logging.getLogger(__name__).error('%s: %s', path, exc)
                failed = True
        if failed:
            raise SystemExit(1)
        return
    if args.command=='import-song':
        from .song_import import run_import
        try:
            print(json.dumps(run_import(args),ensure_ascii=False,indent=2))
        except Exception as exc:
            logging.getLogger(__name__).error('Song import failed (%s): %s', type(exc).__name__, exc)
            raise SystemExit(1) from None
        return
    if args.command=='rebuild-projections':
        from .online import connect
        from .projections import rebuild_projections
        with connect() as c: rebuild_projections(c,args.attempt_id)
        print(json.dumps({'rebuilt':str(args.attempt_id)})); return
    run_selection=json.loads(args.run_selection.read_text()) if getattr(args,'run_selection',None) else None
    if run_selection is not None and (not isinstance(run_selection,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in run_selection.items())): p.error('run-selection must map attempt IDs to run IDs')
    if args.command in ('export-attempts','export-training'):
        from .pipeline.attempts import export_attempts,read_context,utc
        from .pipeline.training import export_dataset
        from .pipeline.direct import read_committed
        from .online import connect
        with connect() as c:
            c.execute('set transaction isolation level repeatable read read only')
            context=read_context(c,utc(args.cutoff),args.environment)
            source=read_committed(c,utc(args.cutoff),args.environment,run_selection=run_selection,
                                  alignment_version=args.alignment_version, kc_labeler_version=args.kc_labeler_version,
                                  reference_grouping_version=args.reference_grouping_version)
        export = export_attempts if args.command=='export-attempts' else export_dataset
        print(json.dumps(export(Objects(os.environ['DATA_ROOT']),source,args.cutoff,args.output_prefix,context,run_selection,args.environment)))
        return
    if args.command=='relay':
        from .outbox_dispatch import relay, close_publisher
        logging.basicConfig(level=logging.INFO, format='%(message)s')
        try:
            print(relay(args.limit, max_duration_s=args.max_seconds))
        except Exception as exc:
            logging.getLogger(__name__).error('stage=outbox.relay outcome=error error_type=%s', type(exc).__name__)
            raise SystemExit(1) from None
        finally:
            close_publisher()
        return
    if args.command=='worker':
        logging.basicConfig(level=logging.INFO, format='%(message)s')
        from .worker import process
        process(args.attempt_id); return
    if args.command=='align':
        from .alignment.service import align
        result=align(args.reference,args.performance,args.attempt_id or str(uuid.uuid4()))
        result.update(song_id=args.song_id,processing_purpose='local-debug')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        print(str(args.output))

if __name__=='__main__': main()
