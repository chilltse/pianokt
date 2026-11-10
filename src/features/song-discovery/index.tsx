import { useCatalogStatus } from '@/features/data/library'
import type { SongMetadata } from '@/types'
import { formatTime, getKey } from '@/utils'
import { Heart, LoaderCircle, Pause, Play, RotateCcw } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useSegmentMarks, useSegmentRecommendations } from './hooks'
import { searchSegments } from './model'
import { useSegmentPreview } from './preview'

export { useSegmentPreview } from './preview'

export function SongSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="my-6">
      <h3 className="mb-2 text-lg font-medium text-gray-900">{title}</h3>
      <div className="overflow-hidden rounded-xl border border-amber-100 bg-white shadow-sm">
        {children}
      </div>
    </section>
  )
}

type Preview = ReturnType<typeof useSegmentPreview>
type Marks = ReturnType<typeof useSegmentMarks>
export function SegmentPreviewButton({ song, preview }: { song: SongMetadata; preview: Preview }) {
  const state = preview.state.id === song.segment_id ? preview.state : null
  const label =
    state?.status === 'loading'
      ? 'Loading'
      : state?.status === 'error'
        ? 'Retry'
        : state?.status === 'playing'
          ? 'Pause'
          : 'Play'
  const Icon =
    label === 'Loading'
      ? LoaderCircle
      : label === 'Retry'
        ? RotateCcw
        : label === 'Pause'
          ? Pause
          : Play
  return (
    <button
      type="button"
      aria-label={`${label} ${song.title}`}
      title={state?.error || label}
      disabled={label === 'Loading'}
      className="rounded p-2 hover:bg-amber-100 disabled:opacity-50"
      onClick={(e) => {
        e.stopPropagation()
        void preview.toggle(song)
      }}
    >
      <Icon size={18} className={label === 'Loading' ? 'animate-spin' : ''} />
    </button>
  )
}
export function SegmentTable({
  songs,
  onSelect,
  preview,
  marks,
}: {
  songs: SongMetadata[]
  onSelect: (song: SongMetadata) => void
  preview: Preview
  marks: Marks
}) {
  return (
    <table className="w-full table-fixed text-sm">
      <thead className="bg-amber-50/50 text-gray-500">
        <tr>
          <th className="p-3 text-left">Title / Composer</th>
          <th className="w-16 text-right">Length</th>
          <th className="w-12">
            <span className="sr-only">Mark</span>
          </th>
          <th className="w-12">
            <span className="sr-only">Preview</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {songs.map((song) => (
          <tr
            key={getKey(song.segment_id ?? song.id, song.source)}
            className="border-t border-amber-50 hover:bg-amber-50/50"
            onClick={() => onSelect(song)}
          >
            <td className="p-3">
              <button
                className="w-full text-left font-medium break-words"
                onClick={(e) => {
                  e.stopPropagation()
                  onSelect(song)
                }}
              >
                {song.title}
              </button>
              <p className="text-xs text-gray-500">{song.composer}</p>
            </td>
            <td className="text-right text-gray-500">{formatTime(song.duration)}</td>
            <td>
              {song.source === 'builtin' && song.segment_id && (
                <button
                  aria-label={`Mark ${song.title}`}
                  aria-pressed={marks.marks.has(song.segment_id)}
                  disabled={marks.busy === song.segment_id || (!!marks.userId && marks.isLoading)}
                  className="p-2 text-rose-500 disabled:opacity-50"
                  onClick={(e) => {
                    e.stopPropagation()
                    void marks.toggle(song.segment_id!)
                  }}
                >
                  <Heart
                    size={18}
                    fill={marks.marks.has(song.segment_id) ? 'currentColor' : 'none'}
                  />
                </button>
              )}
            </td>
            <td>
              {song.source === 'builtin' && song.segment_id && (
                <SegmentPreviewButton song={song} preview={preview} />
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
export function SongDiscovery({
  songs,
  onSelect,
  preview,
}: {
  songs: SongMetadata[]
  onSelect: (song: SongMetadata) => void
  preview: Preview
}) {
  const marks = useSegmentMarks()
  const catalog = useCatalogStatus()
  // Personalized sections only use the current published catalog, never stale offline entries.
  const recommendations = useSegmentRecommendations(catalog.data ?? [])
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const filtered = searchSegments(songs, search)
  const pages = Math.max(1, Math.ceil(filtered.length / 10))
  const current = Math.min(page, pages)
  const marked = (catalog.data ?? songs).filter(
    (s) => s.source === 'builtin' && !!s.segment_id && marks.marks.has(s.segment_id),
  )
  const table = (rows: SongMetadata[], empty: string) =>
    rows.length ? (
      <SegmentTable songs={rows} onSelect={onSelect} preview={preview} marks={marks} />
    ) : (
      <p className="p-8 text-center text-sm text-gray-500">{empty}</p>
    )
  return (
    <>
      {marks.message && (
        <p role="alert" className="text-sm text-rose-700">
          {marks.message}
        </p>
      )}
      {preview.state.status === 'error' && (
        <p role="alert">{preview.state.error}. Use Retry on the segment to try again.</p>
      )}
      <SongSection title="Recommended Songs">
        {recommendations.error ? (
          <p role="alert" className="p-4">
            Unable to load recommendations.{' '}
            <button className="underline" onClick={() => void recommendations.mutate()}>
              Retry
            </button>
          </p>
        ) : recommendations.isLoading || catalog.isLoading ? (
          <p className="p-4">Loading…</p>
        ) : (
          table(
            recommendations.data ?? [],
            marks.userId
              ? 'No personalized recommendations yet.'
              : 'Sign in to see personalized recommendations.',
          )
        )}
      </SongSection>
      <SongSection title="Marked Songs">
        {marks.error ? (
          <p role="alert" className="p-4">
            Unable to load marks.{' '}
            <button className="underline" onClick={() => void marks.mutate()}>
              Retry
            </button>
          </p>
        ) : marks.isLoading ? (
          <p className="p-4">Loading…</p>
        ) : (
          table(marked, marks.userId ? 'No marked segments yet.' : 'Sign in to mark segments.')
        )}
      </SongSection>
      <SongSection title="All Songs">
        <div className="p-3">
          <input
            aria-label="Search songs"
            placeholder="Search Song Title, Composer or Segment Title"
            className="w-full rounded border border-amber-200 p-2 text-sm"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
          />
        </div>
        {catalog.error && (
          <p role="status" className="p-3">
            曲库暂时无法同步，已缓存曲目仍可使用。
            <button onClick={() => void catalog.mutate()}>重试</button>
          </p>
        )}
        {table(
          filtered.slice((current - 1) * 10, current * 10),
          catalog.isLoading ? 'Loading…' : 'No results',
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-amber-100 p-3 text-sm">
          <span>
            Page {current} of {pages} · {filtered.length} results
          </span>
          <div className="flex gap-4">
            <button
              disabled={current <= 1}
              className="disabled:opacity-40"
              onClick={() => setPage(current - 1)}
            >
              Previous
            </button>
            <button
              disabled={current >= pages}
              className="disabled:opacity-40"
              onClick={() => setPage(current + 1)}
            >
              Next
            </button>
          </div>
        </div>
      </SongSection>
    </>
  )
}
