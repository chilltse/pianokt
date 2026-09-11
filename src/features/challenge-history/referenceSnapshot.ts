import { Midi } from '@tonejs/midi'
import type { Song, SongConfig } from '@/types'
/** Immutable snapshot of the assigned hands, transposition and selected range. */
export function referenceSnapshot(song: Song, config: SongConfig, range?: { start: number; end: number }): Uint8Array {
  const midi = new Midi(); midi.header.setTempo(120)
  const start = range?.start ?? 0, end = range?.end ?? song.duration
  const hands = new Map<string, ReturnType<Midi['addTrack']>>()
  for (const note of song.notes) {
    const hand = config.tracks[note.track]?.hand
    if (hand === 'none' || (hand === 'left' && !config.left) || (hand === 'right' && !config.right)) continue
    if (note.time < start || note.time >= end) continue
    const label = hand === 'left' ? 'left' : hand === 'right' ? 'right' : `track-${note.track}`
    let track = hands.get(label)
    if (!track) { track = midi.addTrack(); track.name = label; hands.set(label, track) }
    const pitch = note.midiNote + (config.transpose ?? 0)
    if (pitch < 0 || pitch > 127) throw new Error('Transposed pitch outside MIDI range')
    track.addNote({ midi: pitch, time: note.time-start, duration: Math.min(note.duration,end-note.time), velocity: note.velocity ?? 0.8 })
  }
  return midi.toArray()
}
