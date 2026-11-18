#!/usr/bin/env python3
"""Optional one-time import of the archived sample catalog using the real CLI importer.
New catalogs should use pianokt-data import-song directly. No legacy ID mappings.
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from pianokt_backend.song_import import run_import


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--midi-dir', type=Path, default=ROOT/'scripts/song_catalog/midi')
    p.add_argument('--manifest', type=Path, default=ROOT/'scripts/song_catalog/legacy-manifest.json')
    p.add_argument('--report', type=Path, default=ROOT/'output/song-import-report.json')
    args = p.parse_args()
    report = []
    for meta in json.loads(args.manifest.read_text()):
        try:
            title, separator, composer = meta['title'].rpartition(' - ')
            if not separator:
                title, composer = meta['title'], 'Unknown'
            result = run_import(SimpleNamespace(midi=args.midi_dir/meta['id'], title=title, composer=composer,
                license=meta['license'], source_url=meta['url'], slug=None, song_id=None,
                segment_index=1, segment_title=None, difficulty=meta.get('difficulty',.5), genre=[],dry_run=not args.apply))
            report.append(dict(file=meta['id'],**result))
        except Exception as exc:
            report.append(dict(file=meta['id'],status='failed',error=str(exc)))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    for row in report:
        print(row['file'],row['status'],row.get('error',''))
    return int(any(row['status']=='failed' for row in report))


if __name__=='__main__':
    sys.exit(main())
