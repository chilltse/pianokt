import { fetchSong } from '@/features/data'
import { Player } from '@/features/player/player'
import { getDefaultSongSettings } from '@/features/SongVisualization/utils'
import { getAudioContext } from '@/features/synth/utils'
import type { SongMetadata } from '@/types'
import { getKey } from '@/utils'
import { createStore } from 'jotai'
import { useCallback, useEffect, useRef, useState } from 'react'

export function useSegmentPreview() {
  const generation = useRef(0)
  const active = useRef<{ id: string; player: Player; unsubscribe: () => void } | null>(null)
  const [state, setState] = useState({ id: '', status: 'idle', error: '' })
  const stop = useCallback(() => {
    generation.current++
    active.current?.unsubscribe()
    active.current?.player.stop()
    active.current = null
    setState({ id: '', status: 'idle', error: '' })
  }, [])
  useEffect(
    () => () => {
      generation.current++
      active.current?.unsubscribe()
      active.current?.player.stop()
      active.current = null
    },
    [],
  )
  async function toggle(song: SongMetadata) {
    const id = getKey(song.id, song.source)
    if (active.current?.id === id) {
      const player = active.current.player
      if (player.getTime() >= player.getDuration()) player.seek(0)
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
      // Resume during the click gesture, before MIDI/soundfont downloads finish.
      const audioReady = getAudioContext().resume()
      const [parsed] = await Promise.all([fetchSong(song.id, song.source, song), audioReady])
      if (token !== generation.current) return
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
      const unsubscribe = player.store.sub(player.state, () => {
        if (active.current?.player === player)
          setState({ id, status: player!.isPlaying() ? 'playing' : 'paused', error: '' })
      })
      active.current = { id, player, unsubscribe }
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
