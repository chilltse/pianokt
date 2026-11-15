import type { SongMetadata } from '@/types'

/** Restore folder management and imported songs together when explicitly enabled. */
export const practiceImportsEnabled = import.meta.env.VITE_ENABLE_PRACTICE_IMPORTS === 'true'

export function practiceSongs(songs: SongMetadata[], importsEnabled: boolean): SongMetadata[] {
  return importsEnabled
    ? songs
    : songs.filter((song) => song.source === 'builtin' && !!song.segment_id)
}
