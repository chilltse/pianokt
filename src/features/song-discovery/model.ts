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

export function recommendationSegments(response: unknown, songs: SongMetadata[]): SongMetadata[] {
  if (!response || typeof response !== 'object') return []
  const value = response as { is_demo?: boolean; items?: { segment_id?: string }[] }
  if (value.is_demo !== false || !Array.isArray(value.items)) return []
  const byId = new Map(
    songs.filter((s) => s.source === 'builtin' && s.segment_id).map((s) => [s.segment_id, s]),
  )
  return [...new Set(value.items.map((item) => item?.segment_id))].flatMap((id) =>
    byId.has(id) ? [byId.get(id)!] : [],
  )
}
