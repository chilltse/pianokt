"""User-authorized local feature artifacts; retain actual synthetic history provenance."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

NAMES=['has_black_key','simultaneous_note_count','both_hands','onset_interval_bin_cs','change_count','mean_move_bin']


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_local_features(root, history_path):
    root=Path(root)
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest['kc_labeler_version']!='1.2.0' or manifest['kc_feature_names']!=NAMES:
        raise ValueError('Feature ordering/labeler mismatch')
    candidates=[]
    for path in sorted(root.glob('*/*.json')):
        item=json.loads(path.read_text())
        if item['artifact_type']!='pianokt_recommendation_kc' or item['kc_labeler_version']!='1.2.0' or item['kc_feature_names']!=NAMES:
            raise ValueError('Invalid feature artifact')
        onsets=item['onsets']; times=[o['onset_time'] for o in onsets]
        if not times or times!=sorted(times) or len(times)!=item['reference_events_count']:
            raise ValueError('Invalid onset ordering/count')
        # The artifact has no final note duration. Explicit proxy, not MIDI duration.
        duration=times[-1]-times[0]
        if duration<=0: continue
        candidates.append(dict(song_id=item['song_id'],segment_id=item['segment_id'],
            segment_index=item['segment_index'],song_version=item['midi_sha256'],midi_sha256=item['midi_sha256'],
            kc_labeler_version='1.2.0',feature_artifact_sha256=sha(path),
            vectors=[o['kc_vector'] for o in onsets],duration_seconds=duration,
            duration_basis='first_to_last_onset_span_not_full_midi_duration'))
    if len({c['segment_id'] for c in candidates})!=len(candidates):
        raise ValueError('Duplicate segment identities')
    grouped=json.loads(Path(history_path).read_text()); students=[]
    for student in grouped['students']:
        attempts=[a for v in student['visits'] for a in v['attempts']]
        attempts.sort(key=lambda a:(datetime.fromisoformat(a['ordering_timestamp']),a['attempt_id']))
        history=[]
        for a in attempts:
            if a['kc_labeler_version']!='1.2.0': raise ValueError('History labeler mismatch')
            e=a['events_data']
            if len(e['kc_vectors'])!=len(e['correct']) or e['onset_seconds']!=sorted(e['onset_seconds']):
                raise ValueError('Invalid history labels/order')
            for vector,response in zip(e['kc_vectors'],e['correct']):
                if type(response) not in (int,bool) or response not in (0,1):
                    raise ValueError('Expected binary observed response')
                history.append(dict(kc_vector=vector,response=int(response)))
        students.append(dict(student_id=student['learner_key'],checkpoint_id='end-of-export',history=history))
    return dict(candidates=candidates,students=students,kc_labeler_version='1.2.0',
        source_environment='user_authorized_local_export',history_origin=grouped.get('data_origin',grouped.get('source','unspecified')),
        history_warning=grouped.get('warning'),history_sha256=sha(history_path),catalog_manifest_sha256=sha(root/'manifest.json'),
        ready_status_checked=False,standalone_reference_sidecar_checked=False)
