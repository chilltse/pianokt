export function midiSettingsBlockReason(state: {
  playing: boolean
  countingDown: boolean
  recording: boolean
  finalizing: boolean
  openNotes: boolean
}): string | null {
  if (state.playing || state.countingDown || state.recording)
    return 'Please pause the challenge before changing MIDI settings.'
  if (state.finalizing) return 'Please wait until the recording has finished saving.'
  if (state.openNotes)
    return 'Release all notes before changing MIDI settings. The recording must have no open notes.'
  return null
}

export function usedWebMidi(events: ReadonlyArray<{ timestampSource?: string; type: string; velocity: number }>) {
  return events.some(event => event.timestampSource === 'web_midi' && event.type === 'note_on' && event.velocity > 0)
}
