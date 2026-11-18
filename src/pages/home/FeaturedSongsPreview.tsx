import { useCatalogStatus, useSongManifest } from '@/features/data/library'
import { usePlayer } from '@/features/player'
import { SongPreview } from '@/features/SongPreview/SongPreview'
import { useEventListener, useOnUnmount, usePlayerState } from '@/hooks'
import { Pause, Play } from '@/icons'
import clsx from 'clsx'
import { ChevronDown } from 'lucide-react'
import { useState } from 'react'

export function FeaturedSongsPreview({
  marginTop = 0,
  className,
}: {
  marginTop?: number
  className?: string
}) {
  const playerState = usePlayerState()
  const songs = useSongManifest().filter((song) => song.source === 'builtin')
  const catalog = useCatalogStatus()
  const [currentSong, setCurrentSong] = useState('')
  const selected = songs.find((song) => song.id === currentSong) ?? songs[0]
  const player = usePlayer()

  useEventListener('keydown', (event: Event) => {
    const e = event as KeyboardEvent
    if (e.key === ' ') {
      e.preventDefault()
      player.toggle()
      return
    }
  })

  useOnUnmount(() => player.pause())

  return (
    <div
      className={clsx(
        'relative h-[360px] w-full overflow-hidden rounded-lg shadow-xl',
        'bg-gray-[#2e2e2e]',
        className,
      )}
      style={{ marginTop }}
    >
      {selected ? (
        <SongPreview id={selected.id} source={selected.source} />
      ) : (
        <p role="status" className="p-20 text-white">
          {catalog.error
            ? '曲库暂时不可用'
            : catalog.isLoading
              ? '正在加载曲库…'
              : '尚无已发布曲目'}
          {catalog.error && <button onClick={() => void catalog.mutate()}>重试</button>}
        </p>
      )}
      <div className="absolute top-0 right-0 left-0 z-20 flex h-14 items-center justify-between border-b border-gray-700 bg-gray-800 px-4">
        <div className="flex items-center gap-3">
          <button
            className={clsx(
              'bg-purple-primary hover:bg-purple-hover flex h-8 w-8 items-center justify-center rounded-full text-white transition',
              !playerState.canPlay && 'cursor-progress',
            )}
            disabled={!selected || !playerState.canPlay}
            onClick={() => player.toggle()}
          >
            {playerState.playing ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <span className="text-sm text-gray-400">Preview</span>
        </div>
        <div className="relative text-white">
          <select
            className="appearance-none rounded bg-gray-700 py-1.5 pr-8 pl-3 text-sm font-medium text-gray-200 transition hover:bg-gray-600"
            value={selected?.id ?? ''}
            disabled={!selected}
            onChange={(e) => {
              setCurrentSong(e.target.value)
            }}
          >
            {songs.map((song) => (
              <option key={song.id} value={song.id}>
                {song.title}
              </option>
            ))}
          </select>
          <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-gray-300">
            <ChevronDown className="h-4 w-4" />
          </span>
        </div>
      </div>
    </div>
  )
}
