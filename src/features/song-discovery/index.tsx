import { useFullSongCatalog, useCatalogStatus } from '@/features/data/library'
import type { SongMetadata } from '@/types'
import { formatTime, getKey } from '@/utils'
import { Heart, LoaderCircle, Pause, Play, RotateCcw } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useSegmentMarks } from './hooks'
import { groupSongs, searchSegments } from './model'
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
  const state =
    preview.state.id === getKey(song.id, song.source)
      ? preview.state
      : null
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
      className="cursor-pointer rounded p-2 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
      onClick={(e) => {
        e.stopPropagation()
        void preview.toggle(song)
      }}
    >
      <Icon size={18} className={label === 'Loading' ? 'animate-spin' : ''} />
    </button>
  )
}
export function SegmentMarkButton({ song, marks }: { song: SongMetadata; marks: Marks }) {
  if (song.source !== 'builtin' || !song.segment_id) return null
  const marked = marks.marks.has(song.segment_id)
  const saving = marks.busy === song.segment_id
  return (
    <button
      type="button"
      aria-label={`${saving ? 'Saving' : marked ? 'Unmark' : 'Mark'} ${song.title}`}
      aria-pressed={marked}
      aria-busy={saving}
      disabled={!!marks.busy || (!!marks.userId && marks.isLoading)}
      className="cursor-pointer rounded p-2 text-rose-500 disabled:cursor-not-allowed disabled:opacity-60"
      onClick={(e) => {
        e.stopPropagation()
        void marks.toggle(song.segment_id!)
      }}
    >
      <Heart size={18} fill={marked ? 'currentColor' : 'none'} />
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
            className="cursor-pointer border-t border-amber-50 hover:bg-amber-50/50"
            onClick={() => onSelect(song)}
          >
            <td className="p-3">
              <button
                className="w-full cursor-pointer text-left font-medium break-words disabled:cursor-not-allowed"
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
              <SegmentMarkButton song={song} marks={marks} />
            </td>
            <td>
              {((song.source === 'builtin' && song.segment_id) ||
                song.source === 'local' ||
                song.source === 'base64') && <SegmentPreviewButton song={song} preview={preview} />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
export function SongTree({
  songs,
  onSelect,
  preview,
  marks,
  challenge = false,
}: {
  songs: SongMetadata[]
  onSelect: (song: SongMetadata) => void
  preview: Preview
  marks: Marks
  challenge?: boolean
}) {
  const fullCatalog = useFullSongCatalog()
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  return (
    <div>
      {groupSongs(songs).map(({ id, parts }) => (
        <section key={id} className="border-b border-amber-100">
          <button
            type="button"
            aria-expanded={expanded.has(id)}
            className="flex w-full cursor-pointer items-center gap-3 p-4 text-left hover:bg-amber-50/50"
            onClick={() =>
              setExpanded((previous) => {
                const next = new Set(previous)
                next.has(id) ? next.delete(id) : next.add(id)
                return next
              })
            }
          >
            <span aria-hidden>{expanded.has(id) ? '▾' : '▸'}</span>
            <span>
              <span className="block font-medium">{parts[0].song_title ?? parts[0].title}</span>
              <span className="text-xs text-gray-500">
                {parts[0].composer} · {parts.length} {challenge ? 'challenges' : 'parts'}
              </span>
            </span>
          </button>
          {expanded.has(id) && (
            <div className="pl-4 sm:pl-8">
              {!challenge && (() => {
              const full = fullCatalog.data?.find(song => song.song_id === id)
              return full ? <div className="flex items-center justify-between border-t border-amber-50 p-3 text-sm"><button className="cursor-pointer" onClick={() => onSelect(full)}>Entire Song</button><div className="flex items-center gap-3"><span>{formatTime(full.duration)}</span><SegmentPreviewButton song={full} preview={preview}/></div></div> : <div className="flex justify-between border-t border-amber-50 p-3 text-sm text-gray-500"><span>Entire Song</span><span>{fullCatalog.isLoading ? 'Loading…' : fullCatalog.error ? 'Unable to load' : 'Unavailable'}</span></div>
            })()}
            <SegmentTable
                songs={parts.map((part) => ({
                  ...part,
                  title: part.segment_title || `Part ${part.segment_index}`,
                }))}
                onSelect={onSelect}
                preview={preview}
                marks={marks}
              />
            </div>
          )}
        </section>
      ))}
    </div>
  )
}

export function RecommendationPlaceholder() {
  return (
    <SongSection title="Recommended for You">
      <p className="p-4 text-sm text-gray-500">Personalized recommendations coming soon.</p>
    </SongSection>
  )
}

export function SongDiscovery({
  songs,
  onSelect,
  preview,
  marks,
  compact = false,
  challengedIds,
}: {
  songs: SongMetadata[]
  onSelect: (song: SongMetadata) => void
  preview: Preview
  marks: Marks
  compact?: boolean
  challengedIds?: Set<string | undefined>
}) {
  const catalog = useCatalogStatus()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState('All')
  const filtered = searchSegments(songs, search).filter(
    (song) =>
      !compact ||
      filter === 'All' ||
      (filter === 'Favorites' && marks.marks.has(song.segment_id!)) ||
      (filter === 'Challenged' && challengedIds?.has(song.segment_id)) ||
      (filter === 'Not Tried' && !challengedIds?.has(song.segment_id)),
  )
  const grouped = groupSongs(filtered)
  const pages = Math.max(1, Math.ceil((compact ? grouped.length : filtered.length) / 10))
  const current = Math.min(page, pages)
  const marked = (catalog.data ?? songs).filter(
    (s) => s.source === 'builtin' && !!s.segment_id && marks.marks.has(s.segment_id),
  )
  const table = (rows: SongMetadata[], empty: string) =>
    rows.length ? (
      compact ? (
        <SongTree
          songs={rows}
          onSelect={onSelect}
          preview={preview}
          marks={marks}
          challenge={!!challengedIds}
        />
      ) : (
        <SegmentTable songs={rows} onSelect={onSelect} preview={preview} marks={marks} />
      )
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
      <RecommendationPlaceholder />
      {!compact && (
        <SongSection title="Marked Songs">
          {marks.error ? (
            <p role="alert" className="p-4">
              Unable to load marks.{' '}
              <button className="cursor-pointer underline" onClick={() => void marks.mutate()}>
                Retry
              </button>
            </p>
          ) : marks.isLoading ? (
            <p className="p-4">Loading…</p>
          ) : (
            table(marked, marks.userId ? 'No marked segments yet.' : 'Sign in to mark segments.')
          )}
        </SongSection>
      )}
      <SongSection title={challengedIds ? 'All Challenges' : 'All Songs'}>
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
        {compact && (
          <div className="flex gap-4 px-3 pb-3 text-sm">
            {(challengedIds
              ? ['All', 'Favorites', 'Not Tried', 'Challenged']
              : ['All', 'Favorites']
            ).map((choice) => (
              <button
                key={choice}
                type="button"
                aria-pressed={filter === choice}
                className={`cursor-pointer ${filter === choice ? 'font-semibold text-amber-800' : 'text-gray-600'}`}
                onClick={() => {
                  setFilter(choice)
                  setPage(1)
                }}
              >
                {choice === 'Favorites'
                  ? `Favorites (${songs.filter((s) => marks.marks.has(s.segment_id!)).length})`
                  : choice}
              </button>
            ))}
          </div>
        )}
        {catalog.error && (
          <p role="status" className="p-3">
            曲库暂时无法同步，已缓存曲目仍可使用。
            <button className="cursor-pointer" onClick={() => void catalog.mutate()}>
              重试
            </button>
          </p>
        )}
        {table(
          compact
            ? grouped.slice((current - 1) * 10, current * 10).flatMap((group) => group.parts)
            : filtered.slice((current - 1) * 10, current * 10),
          catalog.isLoading ? 'Loading…' : 'No results',
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-amber-100 p-3 text-sm">
          <span>
            Page {current} of {pages} · {compact ? grouped.length : filtered.length} results
          </span>
          <div className="flex gap-4">
            <button
              disabled={current <= 1}
              className="cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
              onClick={() => setPage(current - 1)}
            >
              Previous
            </button>
            <button
              disabled={current >= pages}
              className="cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
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
