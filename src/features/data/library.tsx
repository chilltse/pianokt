import { SongMetadata, SongSource } from '@/types'
import { getKey } from '@/utils'
import { atom, useAtomValue, useSetAtom } from 'jotai'
import { useEffect } from 'react'
import useSWR from 'swr'
import { localSongsAtom } from '../persist'
import { catalog, fetchFullSongCatalog } from './catalog'

export function useFullSongCatalog() {
  return useSWR('official-full-song-catalog:v1', fetchFullSongCatalog, { revalidateOnFocus: false, dedupingInterval: 60000 })
}

const builtinMetadataAtom = atom<Array<[string, SongMetadata]>>([])

export function useCatalogStatus() {
  const update = useSetAtom(builtinMetadataAtom)
  const result = useSWR('official-song-catalog:segments:v2', () => catalog.refresh(), {
    revalidateOnFocus: false,
    revalidateOnReconnect: true,
    dedupingInterval: 60_000,
  })
  useEffect(() => {
    let active = true
    void catalog.cached().then((rows) => {
      if (active && !result.data) update(rows.map((row) => [getKey(row.id, row.source), row]))
    })
    return () => {
      active = false
    }
  }, [update, result.data])
  useEffect(() => {
    if (result.data) update(result.data.map((row) => [getKey(row.id, row.source), row]))
  }, [update, result.data])
  return result
}

const storageMetadataAtom = atom((get) => {
  const songs = Array.from(get(localSongsAtom).values()).flatMap((x) => x)
  return songs.map((x) => [getKey(x.id, x.source), x]) as [string, SongMetadata][]
})

export const songManifestAtom = atom<Map<string, SongMetadata>>((get) => {
  const builtinMetadata = get(builtinMetadataAtom)
  const storageMetadata = get(storageMetadataAtom)
  return new Map([...builtinMetadata, ...storageMetadata])
})

const songManifestAsListAtom = atom<Array<SongMetadata>>((get) => {
  const songManifest = get(songManifestAtom)
  return Array.from(songManifest.values())
})

export function useSongManifest(): SongMetadata[] {
  useCatalogStatus()
  const songManifestAsList = useAtomValue(songManifestAsListAtom)
  return songManifestAsList
}

export function useSongMetadata(id: string, source: SongSource): SongMetadata | undefined {
  useCatalogStatus()
  const full = useFullSongCatalog()
  const key = getKey(id, source)
  const songManifest = useAtomValue(songManifestAtom)
  return id.startsWith('full:') && source === 'builtin' ? full.data?.find(song => song.id === id) : songManifest.get(key)
}
