import type { Song, SongConfig } from '@/types'
import { Midi } from '@tonejs/midi'
import { referenceSnapshot } from './referenceSnapshot'

describe('referenceSnapshot', () => {
  it('normalizes raw MIDI velocities before exporting', () => {
    const song = {
      duration: 2,
      notes: [
        { type: 'note', midiNote: 76, track: 0, time: 0, duration: 1, velocity: 80, measure: 1 },
        { type: 'note', midiNote: 72, track: 0, time: 1, duration: 1, velocity: 96, measure: 1 },
      ],
    } as Song
    const config = {
      left: true,
      right: true,
      tracks: { 0: { hand: 'right' } },
    } as unknown as SongConfig

    const exported = new Midi(referenceSnapshot(song, config))
    expect(exported.tracks).toHaveLength(1)
    expect(exported.tracks[0].notes.map((note) => Math.round(note.velocity * 127))).toEqual([
      80, 96,
    ])
  })
})
