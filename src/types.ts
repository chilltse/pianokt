import { InstrumentName } from '@/features/synth'
import { KEY_SIGNATURE, NOTE_LABELS } from './features/theory'

export type DifficultyLabel =
  | 'Easiest'
  | 'Easier'
  | 'Easy'
  | 'Medium'
  | 'Hard'
  | 'Hardest'
  // "-" stands for Unknown
  | '-'

export type SongSource = 'local' | 'builtin' | 'generated' | 'base64'
export type SongMetadata = {
  /** Playable entry: official segment UUID, or a local/generated source ID. */
  id: string
  midi_sha256?: string
  storage_path?: string
  song_id?: string
  song_title?: string
  segment_id?: string
  segment_index?: number
  segment_title?: string | null
  composer?: string
  file: string
  title: string
  difficulty: number
  duration: number
  source: SongSource
  url?: string
  license?: string
  handle?: FileSystemFileHandle
  lastModified?: number
  fileSize?: number
}

export interface Size {
  width: number
  height: number
}

export interface Pitch {
  step: string
  octave: number
  alter: number
}

export interface SongNote {
  type: 'note'
  midiNote: number
  track: number
  time: number
  duration: number
  velocity?: number
  measure: number
}

export interface Bpm {
  time: number
  bpm: number
}

export interface Tracks {
  [id: string]: Track
}

export interface Track {
  instrument?: string
  name?: string
  program?: number
}

export type SongMeasure = {
  type: 'measure'
  time: number
  duration: number
  number: number
}

export type OfficialMidiIdentity = {
  song_id: string
  segment_id: string
  segment_index: number
  midi_sha256: string
}

export type Song = {
  officialIdentity?: OfficialMidiIdentity
  tracks: Tracks
  duration: number
  measures: Array<SongMeasure>
  notes: Array<SongNote>
  bpms: Array<Bpm>
  timeSignature?: { numerator: number; denominator: number }
  timeSignatures?: Array<{ time: number; numerator: number; denominator: number }>
  keySignature?: KEY_SIGNATURE
  items: Array<SongNote | SongMeasure>
  ppq: number
  secondsToTicks: (seconds: number) => number
  ticksToSeconds: (seconds: number) => number
}

export type Clef = 'bass' | 'treble'
export type VisualizationMode = 'falling-notes' | 'sheet'
export type Hand = 'both' | 'left' | 'right' | 'none'
export type SongConfig = {
  left: boolean
  right: boolean
  waiting: boolean
  countdownEnabled: boolean
  transpose: number
  loop: {
    enabled: boolean
    range: { start: number; end: number }
  }
  metronome: {
    enabled: boolean
    volume: number
    speed: number
    emphasizeFirst: boolean
  }
  visualization: VisualizationMode
  noteLabels: NOTE_LABELS
  coloredNotes: boolean
  skipMissedNotes: boolean
  keySignature?: KEY_SIGNATURE
  tracks: {
    [trackId: number]: TrackSetting
  }
}

export type TrackSetting = {
  track: Track
  hand: 'left' | 'right' | 'none'
  sound: boolean
  instrument: InstrumentName
}

export type MidiStateEvent = {
  type: 'down' | 'up'
  note: number
  time: number
  velocity?: number
  /**
   * The timestamp supplied by the original browser input event. For Web MIDI
   * this is MIDIMessageEvent.timeStamp; for browser-generated input it is the
   * corresponding DOM event timestamp. It shares the performance.now() time
   * origin in current browsers.
   */
  midiEventTimestamp?: number
  /** Monotonic browser clock sampled when PianoKT handled the input event. */
  performanceNowTimestamp?: number
  /** Distinguishes physical Web MIDI input from browser-generated input. */
  timestampSource?: 'web_midi' | 'browser_keyboard'
}

export type HandSettings = {
  [trackId: string]: {
    hand: Hand | 'none'
  }
}
