"""One reviewed local catalog -> immutable objects -> per-song atomic publication."""
import json
import os
import hashlib
from pathlib import Path
from .song_import import analyze_midi, validate_metadata, slugify, MidiStorage, publish


def scan_catalog(root):
    items, rejected, hashes = [], [], {}
    for path in sorted(Path(root).rglob('metadata.json')):
        if not (path.parent / 'reference.mid').exists():
            continue
        try:
            meta = json.loads(path.read_text())
            if meta.get('status') != 'accepted' or meta.get('hand_separation_verified') is not True:
                raise ValueError('Requires accepted status and verified hand separation')
            full = analyze_midi(path.parent / 'reference.mid')
            slug = meta.get('slug') or slugify(meta['title'], meta['composer'])
            difficulty = meta.get('difficulty', .5)
            validate_metadata(meta['title'],meta['composer'],meta['license'],meta['source_url'],slug,difficulty,1)
            parts = []
            for index, segment in enumerate(meta.get('segments', []), 1):
                filename = Path(segment.get('midi_path', f'segment_{index:03}.mid')).name
                midi = analyze_midi(path.parent / filename)
                start, end = segment['start_sec'], segment['end_sec']
                if start < 0 or end <= start or (full['duration_ms'] > 50000 and not 20 <= end-start <= 40):
                    raise ValueError('Segment boundaries require review')
                identity = hashlib.sha256(json.dumps([full['midi_sha256'],start,end,meta.get('segmentation_version'),meta.get('hand_separation_method')],sort_keys=True).encode()).hexdigest()
                parts.append(dict(item=midi, identity=identity, start=start,end=end))
            if not parts or abs(parts[0]['start'])>.001 or abs(parts[-1]['end']-full['duration_ms']/1000)>.1 or any(abs(a['end']-b['start'])>.001 for a,b in zip(parts,parts[1:])):
                raise ValueError('Segments do not cover the full reference without gaps')
            if full['midi_sha256'] in hashes:
                raise ValueError('Duplicate reference: '+hashes[full['midi_sha256']])
            hashes[full['midi_sha256']] = str(path)
            items.append(dict(path=str(path),meta=meta,slug=slug,full=full,parts=parts,difficulty=difficulty))
        except (ValueError,KeyError,OSError) as exc:
            rejected.append(dict(path=str(path),reason=str(exc)))
    return items,rejected


def sync_song(c, storage, entry, apply=False):
    from psycopg.types.json import Jsonb
    meta, full = entry['meta'], entry['full']
    song = c.execute('select * from public.piano_song_catalog where slug=%s', (entry['slug'],)).fetchone()
    sid = song['song_id'] if song else None
    current = c.execute("select * from public.piano_song_segments where song_id=%s and status<>'archived' order by segment_index",(sid,)).fetchall() if sid else []
    unchanged = len(current)==len(entry['parts']) and all(old['active_midi_sha256']==part['item']['midi_sha256'] and old.get('segmentation_identity') in (None,part['identity']) for old,part in zip(current,entry['parts']))
    result = dict(path=entry['path'],slug=entry['slug'],song_id=str(sid) if sid else None,action='unchanged' if unchanged and song.get('active_full_midi_sha256')==full['midi_sha256'] else 'update' if sid else 'create',archive_segments=[] if unchanged else [str(x['segment_id']) for x in current])
    if not apply: return result
    storage.ensure(full)
    for part in entry['parts']: storage.ensure(part['item'])
    with c.transaction():
        c.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',('song-slug:'+entry['slug'],))
        if sid and not unchanged:
            c.execute("update public.piano_song_segments set status='archived' where song_id=%s and status<>'archived'",(sid,))
        for index,part in enumerate(entry['parts'],1):
            published=publish(c,part['item'],title=meta['title'],composer=meta['composer'],license=meta['license'],source_url=meta['source_url'],slug=entry['slug'],song_id=sid,segment_index=index,difficulty=entry['difficulty'])
            sid=published['song_id']
            c.execute('update public.piano_song_segments set segmentation_identity=%s where segment_id=%s',(part['identity'],published['segment_id']))
        c.execute('''insert into public.piano_song_full_midis(song_id,midi_sha256,storage_path,duration_ms,note_count,track_count,midi_metadata,license,source_url) values(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing''',(sid,full['midi_sha256'],full['storage_path'],full['duration_ms'],full['note_count'],full['track_count'],Jsonb(full['midi_metadata']),meta['license'],meta['source_url']))
        c.execute('update public.piano_song_catalog set active_full_midi_sha256=%s where song_id=%s',(full['midi_sha256'],sid))
    return {**result,'song_id':str(sid),'applied':True}


def add_sync_parser(sub):
    p=sub.add_parser('sync-catalog',help='Audit or synchronize reviewed full-song and segment resources')
    p.add_argument('--catalog-dir',required=True,type=Path)
    p.add_argument('--environment',required=True,choices=['development','test','staging','production'])
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run',action='store_true'); mode.add_argument('--apply',action='store_true')
    p.add_argument('--offline',action='store_true',help='Local validation only, no database comparison')
    p.add_argument('--report',type=Path)


def run_sync(args):
    if args.environment != os.getenv('PIANOKT_ENV','development'): raise ValueError('Environment must match PIANOKT_ENV')
    if args.offline and args.apply: raise ValueError('--offline cannot apply')
    entries,rejected=scan_catalog(args.catalog_dir)
    report=dict(environment=args.environment,rejected=rejected,songs=[])
    if args.offline:
        report['songs']=[dict(path=e['path'],slug=e['slug'],segments=len(e['parts']),midi_sha256=e['full']['midi_sha256']) for e in entries]
    else:
        import psycopg
        from psycopg.rows import dict_row
        storage=MidiStorage(os.environ['SUPABASE_URL'],os.environ['SUPABASE_SERVICE_ROLE_KEY']) if args.apply else None
        with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,row_factory=dict_row) as c:
            for entry in entries:
                try: report['songs'].append(sync_song(c,storage,entry,args.apply))
                except Exception as exc: report['rejected'].append(dict(path=entry['path'],reason=str(exc)))
    if args.report: args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report
