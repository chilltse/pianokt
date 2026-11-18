import type { SongMetadata } from '@/types'
import { loadOfficialMidi } from './catalog'

// Explicit opt-in only. Bounded idle workers share the foreground SHA download pool.
export function prefetchSongs(songs: SongMetadata[], signal: AbortSignal) {
  const connection = (
    navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }
  ).connection
  if (!navigator.onLine || connection?.saveData || /(^|-)2g$/.test(connection?.effectiveType ?? ''))
    return
  let next = 0
  const idle = () =>
    new Promise<void>((resolve) => {
      if ('requestIdleCallback' in window)
        window.requestIdleCallback(() => resolve(), { timeout: 5000 })
      else setTimeout(resolve, 1000)
    })
  const worker = async () => {
    while (!signal.aborted && next < songs.length) {
      await idle()
      if (signal.aborted || !navigator.onLine || document.visibilityState !== 'visible') return
      // Do not start downloads while playing/challenging/uploading.
      if (!['/songs', '/challenge-songs'].includes(location.pathname)) return
      const capacity = await navigator.storage?.estimate().catch(() => undefined)
      if (capacity?.quota && (capacity.usage ?? 0) / capacity.quota > 0.8) return
      const song = songs[next++]
      if (!song) return
      if (!song.midi_sha256 || !song.storage_path) continue
      try {
        await loadOfficialMidi(
          { midi_sha256: song.midi_sha256, storage_path: song.storage_path },
          signal,
        )
      } catch {
        return
      }
    }
  }
  void Promise.all([worker(), worker(), worker()])
}
