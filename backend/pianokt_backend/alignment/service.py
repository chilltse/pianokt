import importlib.util
import json
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
import pandas as pd
from . import prototype
from .tracks import restore_reference_tracks
from ..contracts import AlignmentConfig, check_midi, digest, stable_id

class InsufficientAnchors(ValueError):
    """Timing cannot be estimated reliably; preserve inputs for review."""

def correct_offset(frame, minimum_anchors=3):
    df=frame.copy()
    anchors=df.loc[(df.alignment_type=='match') & df.ref_onset_sec.notna() & df.performance_onset_sec.notna() & (df.ref_pitch==df.performance_pitch)].copy()
    anchors['group']=anchors.ref_onset_sec.round(3)
    grouped=anchors.groupby('group')[['ref_onset_sec','performance_onset_sec']].median()
    if len(grouped)<minimum_anchors: raise InsufficientAnchors('Too few distinct matched onsets')
    delta=(grouped.performance_onset_sec-grouped.ref_onset_sec).to_numpy()
    center=float(np.median(delta)); mad=float(np.median(np.abs(delta-center)))
    kept=delta[np.abs(delta-center)<=max(0.25,3*1.4826*mad)]
    if len(kept)<minimum_anchors: raise InsufficientAnchors('Too few reliable timing anchors')
    offset=float(np.median(kept))
    df['performance_onset_sec_global_aligned']=df.performance_onset_sec-offset
    df['timing_deviation_global_aligned']=df.performance_onset_sec_global_aligned-df.ref_onset_sec
    return df,dict(global_offset_sec=offset,anchors_total=len(delta),anchors_kept=len(kept),mad_sec=mad)

def configured_labels(config):
    spec=importlib.util.spec_from_file_location('_pianokt_labels',prototype.__file__)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TIMING_THRESHOLD_MS=config.timing_threshold_ms
    module.INSERTION_ATTACH_WINDOW_SEC=config.insertion_attach_window_sec
    return module

def align(reference: Path, performance: Path, attempt_id: str, config=AlignmentConfig(), stage_sink=None):
    import parangonar as pa
    for path in (reference,performance):
        check_midi(path)
        if not 0<len(prototype.parse_midi_song_for_hand_inference(path)['notes'])<=config.max_notes:
            raise ValueError('Empty MIDI or too many notes')
    matcher_version=version('parangonar')
    score_hash=digest(reference.read_bytes()); performance_hash=digest(performance.read_bytes())
    run_id=stable_id(attempt_id,score_hash,performance_hash,config.identity,matcher_version)
    with TemporaryDirectory(prefix='pianokt-align-') as tmp:
        out=Path(tmp)/'matches.csv'
        pa.match_midis(ref_midi=str(reference),performance_midi=str(performance),output_file=str(out),shift_onsets_to_zero=False)
        raw=pd.read_csv(out)
    raw_records=json.loads(raw.to_json(orient='records'))
    if stage_sink: stage_sink(run_id,dict(attempt_id=attempt_id,score_version=score_hash,performance_hash=performance_hash,config=config.identity,matcher_version=matcher_version,raw_matches=raw_records))
    required={'alignment_type','ref_pitch','performance_pitch','ref_onset_sec','performance_onset_sec','ref_track'}
    if not required<=set(raw.columns): raise ValueError('Matcher schema mismatch')
    if not raw.alignment_type.isin(['match','insertion','deletion']).all(): raise ValueError('Unknown alignment type')
    normalized=restore_reference_tracks(raw,reference)
    df,diagnostics=correct_offset(normalized,config.minimum_anchors)
    labels=configured_labels(config)
    hands=json.loads(labels.build_two_hand_events_data(df,reference,performance))
    df['reject_reason']=[1 if r.alignment_type=='deletion' else 2 if r.alignment_type=='insertion' else labels.classify_matched_note(r,'ref_pitch','performance_pitch','timing_deviation_global_aligned') for _,r in df.iterrows()]
    df['is_correct']=df.reject_reason==0
    df['alignment_event_id']=[stable_id(run_id,i) for i in range(len(df))]
    for k,v in dict(alignment_run_id=run_id,attempt_id=attempt_id,score_version=score_hash,performance_hash=performance_hash,alignment_version=config.version).items(): df[k]=v
    expected=int(df.alignment_type.isin(['match','deletion']).sum())
    summary=dict(expected_notes=expected,correct_notes=int(df.is_correct.sum()),extra_notes=int((df.alignment_type=='insertion').sum()),missing_notes=int((df.alignment_type=='deletion').sum()),accuracy=float(df.is_correct.sum()/expected) if expected else None)
    return dict(schema_version=1,alignment_run_id=run_id,attempt_id=attempt_id,score_version=score_hash,performance_hash=performance_hash,config=config.identity,matcher_version=matcher_version,diagnostics=diagnostics,summary=summary,raw_matches=raw_records,notes=json.loads(df.to_json(orient='records')),events_data=hands)
