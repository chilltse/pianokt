import json
import mido
import pandas as pd
import pytest
from pianokt_backend.alignment.service import align,correct_offset,InsufficientAnchors,configured_labels
from pianokt_backend.contracts import AlignmentConfig
from pianokt_backend.storage import Objects

def midi(path,shift=0):
    f=mido.MidiFile(); t=mido.MidiTrack(); f.tracks.append(t)
    t.append(mido.MetaMessage('track_name',name='right'))
    t.append(mido.MetaMessage('set_tempo',tempo=500000))
    for i,p in enumerate([60,62,64,65,67,69,71,72,74,76,77,79]):
        t.append(mido.Message('note_on',note=p,velocity=90,time=shift if i==0 else 240))
        t.append(mido.Message('note_off',note=p,velocity=0,time=240))
    f.save(path)

def test_offset():
    df=pd.DataFrame(dict(alignment_type=['match']*7,ref_pitch=[60]*7,performance_pitch=[60]*7,ref_onset_sec=[0,0,0,1,2,3,4],performance_onset_sec=[1,1,1,2,3,4,100]))
    _,d=correct_offset(df)
    assert d['global_offset_sec']==1 and d['anchors_total']==5 and d['anchors_kept']==4
    with pytest.raises(InsufficientAnchors): correct_offset(df.iloc[:3])

def test_config_isolation():
    a=configured_labels(AlignmentConfig(timing_threshold_ms=100)); b=configured_labels(AlignmentConfig(timing_threshold_ms=200))
    row=pd.Series(dict(r=60,p=60,t=0.15))
    assert a.classify_matched_note(row,'r','p','t')==3
    assert b.classify_matched_note(row,'r','p','t')==0
    assert a.combine_reject_reasons([1,2])==1

def test_v11_error_codes_have_fixed_priority():
    labels=configured_labels(AlignmentConfig(timing_threshold_ms=100))
    assert labels.classify_matched_note(pd.Series(dict(r=60,p=61,t=.01)),'r','p','t') == 2
    assert labels.classify_matched_note(pd.Series(dict(r=60,p=60,t=-.101)),'r','p','t') == 3
    assert labels.classify_matched_note(pd.Series(dict(r=60,p=61,t=.3)),'r','p','t') == 4
    assert labels.classify_matched_note(pd.Series(dict(r=60,p=60,t=.1)),'r','p','t') == 0

def test_immutable(tmp_path):
    o=Objects(str(tmp_path)); o.put('a',b'a'); o.put('a',b'a')
    with pytest.raises(ValueError): o.put('a',b'b')
    with pytest.raises(ValueError): o.put('../escape',b'a')

def test_real_alignment_replay_and_quality(tmp_path):
    ref=tmp_path/'ref.mid'; perf=tmp_path/'perf.mid'; midi(ref); midi(perf,480)
    stages=[]; r=align(ref,perf,'fixture',stage_sink=lambda key,data:stages.append(data))
    assert r['summary']['expected_notes']==12 and r['summary']['correct_notes']==12
    assert abs(r['diagnostics']['global_offset_sec']-0.5)<0.001
    assert all(event[0]==[] for event in r['events_data']['pitches'])
    assert len(stages)==1 and len(stages[0]['raw_matches'])==12
    r.update(learner_key='synthetic',song_id='scale',segment_id='scale-part-2',segment_index=2,midi_sha256='a'*64)
    from pianokt_backend.storage_layout import silver_result_key
    from pianokt_backend.pipeline.attempts import validate_events
    store=Objects(str(tmp_path/'data'))
    key=silver_result_key(r['alignment_run_id']); store.json(key,r); store.json(key,r)
    validate_events(json.loads(store.read(key))['events_data'])
    bad=json.loads(json.dumps(r)); bad['song_id']='changed'
    with pytest.raises(ValueError,match='Immutable object conflict'): store.json(key,bad)

def test_demo():
    from pianokt_backend.inference import DemoRecommender
    m=DemoRecommender()
    assert 'NOT-TRAINED' in m.version
    r=m.recommend([],[],[dict(song_id='a',segment_id='part',segment_index=1,title='A',difficulty=0.3,genres=[])],1)
    assert r[0]['predicted_accuracy'] is None

def test_training_cutoff_and_mask(tmp_path):
    from test_direct_export import setup, CUTOFF
    from pianokt_backend.pipeline.direct import CommittedResults
    from pianokt_backend.pipeline.training import export_dataset
    from pianokt_backend.publication import read_bundle
    store, _, context, rows = setup(tmp_path)
    result=export_dataset(store,CommittedResults(rows),CUTOFF,None,context)
    _, files=read_bundle(store, 'gold/knowledge_tracing/'+result['dataset_version'])
    data=json.loads(files['training.json'])
    assert [r['correct'] for r in data['examples']]==[1,0,1,0]
    result=export_dataset(store,CommittedResults(rows),'2025-02-01T00:00:00Z',None,context)
    _, files=read_bundle(store, 'gold/knowledge_tracing/'+result['dataset_version'])
    assert json.loads(files['training.json'])['examples']==[]
