import importlib.util
from pathlib import Path
from pianokt_backend.alignment.service import align

def test_meta_track_and_two_hands(tmp_path):
    p=Path(__file__).resolve().parents[2]/'scripts/make_demo_midis.py'
    spec=importlib.util.spec_from_file_location('fixture',p); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    ref=tmp_path/'r.mid'; perf=tmp_path/'p.mid'; mod.write(ref); mod.write(perf,480)
    result=align(ref,perf,'two-hands')
    assert result['summary']['correct_notes']==24
    hands=result['events_data']['data']
    assert set(hands['left_hand']['reject_reason'])=={0}
    assert set(hands['right_hand']['reject_reason'])=={0}
    assert max(hands['left_hand']['pitches'][0])<min(hands['right_hand']['pitches'][0])
    assert set(n['ref_track'] for n in result['notes'])=={1.0,2.0}

def test_explicit_left_only(tmp_path):
    import mido
    from pianokt_backend.alignment.prototype import parser_infer_hands,parse_midi_song_for_hand_inference
    f=mido.MidiFile(); track=mido.MidiTrack();f.tracks.append(track)
    track.append(mido.MetaMessage('track_name',name='left'))
    track.append(mido.Message('note_on',note=48,velocity=90))
    track.append(mido.Message('note_off',note=48,time=480))
    p=tmp_path/'left.mid';f.save(p)
    assert parser_infer_hands(parse_midi_song_for_hand_inference(p),'test')=={'left':0,'right':None}
