import json
import mido
import pandas as pd
import pytest
from pianokt_backend.alignment.service import align,correct_offset,InsufficientAnchors,configured_labels
from pianokt_backend.contracts import AlignmentConfig
from pianokt_backend.storage import Objects
from pianokt_backend.pipeline.tables import Tables
from pianokt_backend.pipeline.jobs import catch_up

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
    assert a.classify_matched_note(row,'r','p','t')==5
    assert b.classify_matched_note(row,'r','p','t')==0
    assert a.combine_reject_reasons([1,2])==3

def test_immutable(tmp_path):
    o=Objects(str(tmp_path)); o.put('a',b'a'); o.put('a',b'a')
    with pytest.raises(ValueError): o.put('a',b'b')
    with pytest.raises(ValueError): o.put('../escape',b'a')

def test_real_alignment_replay_and_quality(tmp_path):
    ref=tmp_path/'ref.mid'; perf=tmp_path/'perf.mid'; midi(ref); midi(perf,480)
    stages=[]; r=align(ref,perf,'fixture',stage_sink=lambda key,data:stages.append(data))
    assert r['summary']['expected_notes']==12 and r['summary']['correct_notes']==12
    assert abs(r['diagnostics']['global_offset_sec']-0.5)<0.001
    assert set(r['events_data']['data']['left_hand']['reject_reason'])=={-1}
    assert len(stages)==1 and len(stages[0]['raw_matches'])==12
    r.update(learner_key='synthetic',song_id='scale')
    store=Objects(str(tmp_path/'raw')); tables=Tables(str(tmp_path/'lake'))
    key=f"alignment/{r['alignment_run_id']}/result.json"; store.json(key,r)
    assert catch_up(store,tables)==1
    assert catch_up(store,tables)==0
    assert catch_up(store,tables,True)==1
    assert len(tables.rows('gold/fact_midi_alignment'))==12
    assert len(tables.rows('gold/hand_events'))==24
    bad=json.loads(json.dumps(r)); bad['alignment_run_id']='bad'; bad['notes'][0]['reject_reason']=99
    store.json('alignment/bad/result.json',bad)
    with pytest.raises(RuntimeError): catch_up(store,tables)
    assert len(tables.rows('ops/completed_runs'))==1
    assert len(tables.rows('quality/alignment_runs'))==1

def test_demo():
    from pianokt_backend.inference import DemoRecommender
    m=DemoRecommender()
    assert 'NOT-TRAINED' in m.version
    r=m.recommend([],[],[dict(song_id='a',title='A',difficulty=0.3,genres=[])],1)
    assert r[0]['predicted_accuracy'] is None

def test_training_cutoff_and_mask(tmp_path):
    from pianokt_backend.pipeline.training import export_dataset
    store=Objects(str(tmp_path))
    tables=Tables(str(tmp_path/'lake'))
    from pianokt_backend.pipeline.tables import ENVELOPE_SCHEMA
    tables.merge('ops/completed_runs',[dict(event_id='r',event_type='alignment.completed',source_key='fixture',payload_json='{}')],ENVELOPE_SCHEMA,'event_id')
    r=dict(attempt_id='a',alignment_run_id='r',learner_key='s',attempt_created_at='2026-01-01T00:00:00+00:00',practice_metadata={'settings':{'played_until_sec':1}},events_data={'data':{'right_hand':{'reject_reason':[0,-1,1],'onset_time':[0,500,2000],'pitches':[[60],[-1],[62]]}}})
    r['alignment_completed_at']='2026-01-01T00:01:00+00:00'
    store.json('alignment/r/result.json',r)
    export_dataset(store,tables,'2026-02-01T00:00:00Z','datasets/test.json')
    data=json.loads(store.read('datasets/test.json'))
    assert len(data['examples'])==1
    export_dataset(store,tables,'2025-02-01T00:00:00Z','datasets/empty.json')
    assert json.loads(store.read('datasets/empty.json'))['examples']==[]
