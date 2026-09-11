"""Resolve Partitura's renumbered tracks back to original MIDI track indices."""
from collections import defaultdict
import mido
import pandas as pd

def restore_reference_tracks(frame,reference):
    signatures=defaultdict(set)
    midi=mido.MidiFile(str(reference))
    for track_id,track in enumerate(midi.tracks):
        tick=0
        for message in track:
            tick+=message.time
            if message.type=='note_on' and message.velocity>0:
                signatures[(tick,message.note,message.channel,message.velocity)].add(track_id)
    result=frame.copy(); result['ref_matcher_track']=result.ref_track
    mapping={}
    for track,rows in result.loc[result.ref_track.notna()].groupby('ref_track'):
        candidates=None
        for _,row in rows.iterrows():
            signature=tuple(int(row['ref_'+k]) for k in ('onset_tick','pitch','channel','velocity'))
            matches=signatures.get(signature,set())
            candidates=matches.copy() if candidates is None else candidates & matches
        if not candidates or len(candidates)!=1:
            raise ValueError('Ambiguous reference track mapping; use explicit distinct score tracks')
        mapping[track]=next(iter(candidates))
    result['ref_track']=result.ref_track.map(mapping)
    return result
