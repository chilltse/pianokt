import { parseMidi } from '@/features/parsers'
import { Song, SongMetadata, SongSource } from '@/types'
import { base64ToBytes } from '@/utils'
import type { SWRResponse } from 'swr'
import useSWRImmutable from 'swr/immutable'
import * as persistence from '../persist/persistence'
import { catalog, loadOfficialMidi } from './catalog'
import { useCatalogStatus, useSongMetadata } from './library'

function getBase64Song(data: string): Song {
  const binaryMidi = base64ToBytes(data)
  return parseMidi(binaryMidi)
}

async function fetchSong(id: string, source: SongSource, identity?: SongMetadata): Promise<Song> {
  if (source === 'builtin') {
    await catalog.cached()
    let metadata = identity ?? catalog.resolve(id)
    if (!metadata) {
      await catalog.refresh()
      metadata = catalog.resolve(id)
    }
    if (
      !metadata?.midi_sha256 ||
      !metadata.storage_path ||
      !metadata.song_id ||
      !metadata.segment_id ||
      !metadata.segment_index
    )
      throw new Error('Unknown official song')
    const bytes = await loadOfficialMidi({
      midi_sha256: metadata.midi_sha256,
      storage_path: metadata.storage_path,
    })
    return {
      ...parseMidi(new Uint8Array(bytes)),
      officialIdentity: {
        song_id: metadata.song_id!,
        segment_id: metadata.segment_id!,
        segment_index: metadata.segment_index!,
        midi_sha256: metadata.midi_sha256,
      },
    }
  } else if (source === 'base64') {
    return getBase64Song(id)
  } else if (source === 'local') {
    await persistence.initialize()

    return persistence
      .getSongHandle(id)
      .then((handle) => {
        return handle?.getFile()
      })
      .then((file) => {
        if (!file) {
          throw new Error(`Could not get song for ${id}, ${source}`)
        }
        return file.arrayBuffer()
      })
      .then((buffer: ArrayBuffer) => parseMidi(new Uint8Array(buffer)))
  }

  return Promise.reject(new Error(`Could not get song for ${id}, ${source}`))
}

export function useSong(id: string, source: SongSource): SWRResponse<Song, any, any> {
  const metadata = useSongMetadata(id, source)
  const status = useCatalogStatus()
  return useSWRImmutable(
    source === 'builtin' && !metadata && !status.data && !status.error
      ? null
      : [id, source, metadata?.midi_sha256],
    () => {
      if (source === 'builtin' && !metadata)
        throw status.error ?? new Error('Unknown official song')
      return fetchSong(id, source, metadata)
    },
  )
}
