import type { SongMetadata } from '@/types'

export function searchSegments(songs: SongMetadata[], search: string) {
  const terms = search
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .trim()
    .split(/\s+/)
  return songs.filter((song) => {
    const text = [song.title, song.song_title, song.segment_title, song.composer]
      .join(' ')
      .normalize('NFD')
      .replace(/\p{Diacritic}/gu, '')
      .toLowerCase()
    return terms.every((term) => text.includes(term))
  })
}
