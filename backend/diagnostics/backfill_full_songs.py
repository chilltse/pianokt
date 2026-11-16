"""Incremental full-song repair. No segment, song identity or activity mutations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pianokt_backend.song_import import analyze_midi, MidiStorage


def run(root, output, apply=False):
    local=[]
    for p in sorted(root.rglob('metadata.json')):
        if any('backup' in part.lower() for part in p.parts) or not (p.parent/'reference.mid').exists(): continue
        meta=json.loads(p.read_text())
        if meta.get('status')!='accepted': continue
        full=analyze_midi(p.parent/'reference.mid')
        if meta.get('sha256') and meta['sha256']!=full['midi_sha256']: continue
        parts={hashlib.sha256(f.read_bytes()).hexdigest() for f in p.parent.glob('segment_*.mid')}
        local.append((p,meta,full,parts))
    storage=MidiStorage(os.environ['SUPABASE_URL'],os.environ['SUPABASE_SERVICE_ROLE_KEY'])
    # Keep operational backups and reports readable only by this account.
    os.umask(0o077)
    report={'applied':apply,'songs':[],'unmatched':[]}
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,row_factory=dict_row) as c:
        songs=c.execute("select * from piano_song_catalog where status='published' order by song_id").fetchall()
        before=c.execute('select * from piano_song_full_midis').fetchall()
        segments=c.execute('select * from piano_song_segments order by segment_id').fetchall()
        midis=c.execute('select * from piano_song_midis order by segment_id,midi_sha256').fetchall()
        backup={'songs':songs,'full_midis':before,'segments':segments,'midis':midis}
        output.mkdir(parents=True,exist_ok=True)
        if apply:
            backup_path=output/'before.json'
            if not backup_path.exists(): backup_path.write_text(json.dumps(backup,default=str,ensure_ascii=False,indent=2))
        for song in songs:
            sid=str(song['song_id']); active=song['active_full_midi_sha256']
            current=next((r for r in before if str(r['song_id'])==sid and r['midi_sha256']==active),None)
            if current:
                report['songs'].append({'song_id':sid,'title':song['title'],'action':'existing','hash':active});continue
            if c.execute("select 1 from piano_data_exclusions where scope='song' and identity=%s",(sid,)).fetchone():
                report['unmatched'].append({'song_id':sid,'reason':'excluded'});continue
            remote_hashes={r['midi_sha256'] for r in midis if str(r['song_id'])==sid}
            candidates={}
            for path,meta,full,parts in local:
                # Producer's original content-derived slug OR all stored segment
                # bytes and source provenance. Titles never establish identity.
                hash_slug=song['slug']=='pianokt-'+full['midi_sha256'][:16]
                source=meta.get('source_url')==song['source_url']
                segment_proof=bool(remote_hashes) and remote_hashes <= parts
                if source and (hash_slug or segment_proof): candidates[full['midi_sha256']]=(path,meta,full)
            if len(candidates)!=1:
                report['unmatched'].append({'song_id':sid,'title':song['title'],'reason':'missing_or_ambiguous_reference','candidate_hashes':list(candidates)});continue
            path,meta,full=next(iter(candidates.values()))
            row={'song_id':sid,'title':song['title'],'action':'fill','path':str(path),'hash':full['midi_sha256']}
            report['songs'].append(row)
            if not apply:continue
            for retry in range(3):
                try:
                    row['uploaded']=storage.ensure(full)
                    break
                except Exception:
                    if retry == 2: raise
            # Persist progress before continuing; retries independently re-audit DB.
            (output/'progress.json').write_text(json.dumps(report,default=str,ensure_ascii=False,indent=2))
            with c.transaction():
                locked=c.execute('select * from piano_song_catalog where song_id=%s for update',(sid,)).fetchone()
                if locked!=song:raise ValueError('Concurrent catalog modification')
                c.execute('''insert into piano_song_full_midis(song_id,midi_sha256,storage_path,duration_ms,note_count,track_count,midi_metadata,license,source_url)
                    values(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing''',
                    (sid,full['midi_sha256'],full['storage_path'],full['duration_ms'],full['note_count'],full['track_count'],Jsonb(full['midi_metadata']),song['license'],song['source_url']))
                c.execute('update piano_song_catalog set active_full_midi_sha256=%s where song_id=%s',(full['midi_sha256'],sid))
        assert c.execute('select * from piano_song_segments order by segment_id').fetchall()==segments
        assert c.execute('select * from piano_song_midis order by segment_id,midi_sha256').fetchall()==midis
        report['segments_unchanged']=True
        (output/('apply.json' if apply else 'dry-run.json')).write_text(json.dumps(report,default=str,ensure_ascii=False,indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog-dir',type=Path,required=True);p.add_argument('--report-dir',type=Path,required=True)
    p.add_argument('--apply',action='store_true');a=p.parse_args()
    r=run(a.catalog_dir,a.report_dir,a.apply)
    print(json.dumps({'existing':sum(x['action']=='existing' for x in r['songs']),'fill':sum(x['action']=='fill' for x in r['songs']),'unmatched':r['unmatched']},ensure_ascii=False))
