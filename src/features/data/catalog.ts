import { supabase } from '@/features/auth/supabase'
import type { SongMetadata } from '@/types'
import { get, set } from 'idb-keyval'
import { binaryStore, createMidiLoader } from './midiCache'

export type CatalogRow = {
  song_id: string
  segment_id: string
  segment_index: number
  segment_title?: string | null
  title: string
  difficulty: number
  midi_sha256: string
  storage_path: string
  duration_ms: number
  license?: string
  source_url?: string
  composer?: string
}
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
function validRow(row: CatalogRow): boolean {
  return (
    !!row &&
    typeof row.song_id === 'string' &&
    uuid.test(row.song_id) &&
    typeof row.segment_id === 'string' &&
    uuid.test(row.segment_id) &&
    Number.isInteger(row.segment_index) &&
    row.segment_index > 0 &&
    typeof row.title === 'string' &&
    typeof row.midi_sha256 === 'string' &&
    /^[a-f0-9]{64}$/.test(row.midi_sha256) &&
    row.storage_path === `midi/${row.midi_sha256}.mid` &&
    Number.isFinite(row.duration_ms) &&
    row.duration_ms > 0
  )
}
function cachedRows(value: unknown): SongMetadata[] {
  if (!Array.isArray(value)) return []
  return value.filter(
    (row) => validRow(row) && row.source === 'builtin' && row.id === row.segment_id,
  )
}

export function metadata(row: CatalogRow): SongMetadata {
  if (!validRow(row)) throw new Error('Invalid official catalog identity or MIDI metadata')
  return {
    ...row,
    id: row.segment_id,
    song_title: row.title,
    title: `${row.title} — ${row.segment_title || `Segment ${row.segment_index}`}`,
    source: 'builtin',
    file: row.storage_path,
    duration: row.duration_ms / 1000,
    url: row.source_url,
  }
}
export function createCatalog(
  query: () => Promise<CatalogRow[]>,
  store = {
    get: () => get<SongMetadata[]>('official-catalog:v2:segments'),
    set: (rows: SongMetadata[]) => set('official-catalog:v2:segments', rows),
  },
) {
  let rows: SongMetadata[] = []
  let pending: Promise<SongMetadata[]> | undefined
  let hydrated: Promise<SongMetadata[]> | undefined
  return {
    cached: async () => {
      await (hydrated ??= Promise.resolve()
        .then(() => store.get())
        .then((value) => (rows = cachedRows(value)))
        .catch(() => rows))
      return rows
    },
    refresh: () =>
      (pending ??= (async () => {
        await (hydrated ??= Promise.resolve()
          .then(() => store.get())
          .then((value) => (rows = cachedRows(value)))
          .catch(() => rows))
        const next = (await query())
          .map(metadata)
          .sort(
            (a, b) =>
              (a.composer ?? '').localeCompare(b.composer ?? '') ||
              (a.song_title ?? '').localeCompare(b.song_title ?? '') ||
              a.song_id!.localeCompare(b.song_id!) ||
              a.segment_index! - b.segment_index!,
          )
        rows = next
        try {
          await store.set(next)
        } catch {
          /* Directory remains usable in memory. */
        }
        return rows
      })().finally(() => {
        pending = undefined
      })),
    resolve: (id: string) => rows.find((row) => row.id === id),
  }
}
export const catalog = createCatalog(async () => {
  if (!supabase) throw new Error('Supabase song catalog is not configured')
  const { data, error } = await supabase.rpc('piano_public_catalog')
  if (error) throw error
  return data as CatalogRow[]
})
export const loadOfficialMidi = createMidiLoader(binaryStore, async (path) => {
  if (!supabase) throw new Error('Supabase is not configured')
  const { data } = supabase.storage.from('piano-midi').getPublicUrl(path)
  const response = await fetch(data.publicUrl, { signal: AbortSignal.timeout(60_000) })
  if (!response.ok) throw new Error(`MIDI download failed: ${response.status}`)
  return response.arrayBuffer()
})
