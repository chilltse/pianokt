#!/usr/bin/env python3
"""Read-only producer audit plus optional staging of legacy references for review.
Never infers hand identity from pitch or silently publishes segmentation.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import pretty_midi


def legacy_hands(pm):
    """User-authorized convention for the legacy 17 only; not a general inference."""
    tracks = [track for track in pm.instruments if track.notes and not track.is_drum]
    if len(tracks) == 1:
        return {'R': tracks[0]}
    if len(tracks) != 2:
        return None
    means = [sum(note.pitch for note in track.notes) / len(track.notes) for track in tracks]
    if means[0] == means[1]:
        return None
    low, high = sorted(range(2), key=lambda index: means[index])
    return {'L': tracks[low], 'R': tracks[high]}


def audit(producer, repository, staging=None):
    sys.path.insert(0, str(producer))
    import phrase_v5
    reviewed = []
    for path in sorted((producer / 'output').rglob('metadata.json')):
        if 'accepted' not in path.parts or any('backup' in part for part in path.parts):
            continue
        record = json.loads(path.read_text())
        reference = path.parent / 'reference.mid'
        reviewed.append(dict(directory=str(path.parent), title=record.get('title'),
            source_url=record.get('source_url'), reference_exists=reference.exists(),
            sha256=hashlib.sha256(reference.read_bytes()).hexdigest() if reference.exists() else None,
            segments=len(list(path.parent.glob('segment_*.mid'))), segmentation_version=record.get('segmentation_version')))
    legacy = []
    for record in json.loads((repository/'scripts/song_catalog/legacy-manifest.json').read_text()):
        source = repository/'scripts/song_catalog/midi'/Path(record['file']).name
        row = dict(record, status='manual_review', segments=[])
        try:
            raw = source.read_bytes(); sha = hashlib.sha256(raw).hexdigest()
            pm = pretty_midi.PrettyMIDI(str(source))
            row.update(sha256=sha, duration_sec=pm.get_end_time(), tracks=[i.name for i in pm.instruments if i.notes])
            hands = legacy_hands(pm)
            row["hand_separation_method"] = "legacy17_user_authorized_mean_pitch_v1"
            row["hand_mapping"] = {side: pm.instruments.index(track) for side, track in (hands or {}).items()}
            if hands is None:
                row['reason'] = 'requires_one_or_two_tracks_with_distinct_mean_pitch'
            else:
                bounds, reason = ([(0, pm.get_end_time(), {})], None) if pm.get_end_time() <= 50 else phrase_v5.segment_bounds(pm, {'L': hands.get('L', pretty_midi.Instrument(0)), 'R': hands['R']})
                if reason: row['reason'] = reason
                else:
                    row.update(status='ready_for_listening_review', segmentation_version='phrase_v5', hand_separation_verified=True,
                        segments=[dict(index=i,start_sec=a,end_sec=b,duration_sec=b-a) for i,(a,b,_) in enumerate(bounds,1)])
            if staging:
                dest=staging/sha[:16]; dest.mkdir(parents=True,exist_ok=True)
                target=dest/'reference.mid'
                if target.exists() and target.read_bytes()!=raw: raise ValueError('Immutable reference conflict')
                target.write_bytes(raw)
                if row['status']=='ready_for_listening_review':
                    for i,(a,b,_) in enumerate(bounds,1):
                        target=dest/f'segment_{i:03d}.mid'
                        if pm.get_end_time() <= 50: target.write_bytes(raw)
                        else:
                            import build
                            build.write_clip(pm,{'L': hands.get('L', pretty_midi.Instrument(0)), 'R': hands['R']},a,b,target)
                (dest/'metadata.json').write_text(json.dumps(row,ensure_ascii=False,indent=2))
        except Exception as error: row['reason']=str(error)
        legacy.append(row)
    hashes={}
    for row in reviewed:
        if row['sha256']: hashes.setdefault(row['sha256'],[]).append(row['directory'])
    return dict(reviewed_songs=len(reviewed),reviewed_segments=sum(r['segments'] for r in reviewed),
        missing_references=[r for r in reviewed if not r['reference_exists']],
        exact_content_duplicates={k:v for k,v in hashes.items() if len(v)>1},
        legacy=legacy,reviewed=reviewed,
        note='Titles do not prove duplicate work identity. Explicit hand verification required before publication.')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--catalog-dir',type=Path,required=True)
    parser.add_argument('--repository',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--stage-legacy',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    result=audit(args.catalog_dir,args.repository,args.stage_legacy)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('legacy','reviewed')}))
