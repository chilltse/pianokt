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

/** Titles are display-only: two identically named works remain distinct. */
export function groupSongs(segments: SongMetadata[]) {
  const groups = new Map<string, SongMetadata[]>()
  for (const segment of segments) {
    const key = segment.source === 'builtin' ? segment.song_id : `${segment.source}:${segment.id}`
    if (!key) continue
    const group = groups.get(key) ?? []
    if (!group.some((s) => s.id === segment.id)) group.push(segment)
    groups.set(key, group)
  }
  return [...groups].map(([id, parts]) => ({
    id,
    parts: parts.sort((a, b) => (a.segment_index ?? 0) - (b.segment_index ?? 0)),
  }))
}
