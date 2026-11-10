import { loadOfficialMidi } from '@/features/data/catalog'
import { parseMidi } from '@/features/parsers'
import { Player } from '@/features/player/player'
import { getDefaultSongSettings } from '@/features/SongVisualization/utils'
import type { SongMetadata } from '@/types'
import { createStore } from 'jotai'
import { useCallback, useEffect, useRef, useState } from 'react'

export function useSegmentPreview() {
  const generation = useRef(0)
  const active = useRef<{ id: string; player: Player } | null>(null)
  const [state, setState] = useState({ id: '', status: 'idle', error: '' })
  const stop = useCallback(() => {
    generation.current++
    active.current?.player.stop()
    active.current = null
    setState({ id: '', status: 'idle', error: '' })
  }, [])
  useEffect(
    () => () => {
      generation.current++
      active.current?.player.stop()
      active.current = null
    },
    [],
  )
  async function toggle(song: SongMetadata) {
    const id = song.segment_id!
    if (active.current?.id === id) {
      const player = active.current.player
      player.toggle()
      setState({ id, status: player.isPlaying() ? 'playing' : 'paused', error: '' })
      return
    }
    stop()
    const token = generation.current
    setState({ id, status: 'loading', error: '' })
    // A separate existing Player instance prevents stale async setup from touching modal playback.
    let player: Player | undefined
    try {
      const bytes = await loadOfficialMidi({
        midi_sha256: song.midi_sha256!,
        storage_path: song.storage_path!,
      })
      if (token !== generation.current) return
      const parsed = parseMidi(new Uint8Array(bytes))
      player = new Player(createStore(), false)
      const defaults = getDefaultSongSettings(parsed)
      await player.setSong(parsed, {
        ...defaults,
        waiting: false,
        countdownEnabled: false,
        metronome: { ...defaults.metronome, enabled: false },
      })
      if (token !== generation.current) {
        player.stop()
        return
      }
      active.current = { id, player }
      player.store.sub(player.state, () => {
        if (active.current?.player === player)
          setState({ id, status: player!.isPlaying() ? 'playing' : 'paused', error: '' })
      })
      player.play()
    } catch (e) {
      player?.stop()
      if (token === generation.current)
        setState({
          id,
          status: 'error',
          error: e instanceof Error ? e.message : 'Unable to play MIDI',
        })
    }
  }
  return { state, stop, toggle }
}
