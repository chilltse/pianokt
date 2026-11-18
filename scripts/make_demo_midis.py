"""Generate synthetic two-hand inputs, with a 500 ms global performance delay."""
from pathlib import Path
import mido

def write(path,shift=0):
    f=mido.MidiFile(); meta=mido.MidiTrack(); f.tracks.append(meta)
    meta.append(mido.MetaMessage('set_tempo',tempo=500000))
    for hand,base in [('left',48),('right',72)]:
        track=mido.MidiTrack(); f.tracks.append(track)
        track.append(mido.MetaMessage('track_name',name=hand))
        for i,interval in enumerate([0,2,4,5,7,9,11,12,11,9,7,5]):
            track.append(mido.Message('note_on',note=base+interval,velocity=90,time=shift if i==0 else 240))
            track.append(mido.Message('note_off',note=base+interval,velocity=0,time=240))
    f.save(path)

if __name__=='__main__':
    out=Path('data/demo'); out.mkdir(parents=True,exist_ok=True)
    write(out/'reference.mid'); write(out/'performance.mid',480)
    print('Created synthetic two-hand MIDI in data/demo')
