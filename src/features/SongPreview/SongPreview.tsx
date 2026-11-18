import { useSong } from '@/features/data'
import { usePlayer } from '@/features/player'
import { getHandSettings, SongVisualizer } from '@/features/SongVisualization'
import { SongSource } from '@/types'
import { useEffect, useMemo } from 'react'
import { getDefaultSongSettings } from '../SongVisualization/utils'

interface SongPreviewProps {
  id: string
  source: SongSource
}

function SongPreview({ id, source }: SongPreviewProps) {
  const { data: song, error } = useSong(id, source)
  const player = usePlayer()
  const songConfig = useMemo(() => {
    const defaults = getDefaultSongSettings(song)
    return {
      ...defaults,
      countdownEnabled: false,
      metronome: { ...defaults.metronome, enabled: false, volume: 0 },
    }
  }, [song])

  useEffect(() => {
    if (!song) {
      player.stop()
      return
    }
    player.setSong(song, songConfig)
  }, [song, player, songConfig])

  if (error) return <p role="alert">无法加载 MIDI：{String(error.message ?? error)}</p>
  if (!song) return <p role="status">正在加载 MIDI…</p>

  return (
    <SongVisualizer
      song={song}
      config={songConfig}
      getTime={() => player.getTime()}
      hand="both"
      handSettings={getHandSettings(songConfig)}
    />
  )
}

export type { SongPreviewProps }
export { SongPreview }
