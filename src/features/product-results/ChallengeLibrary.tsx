import { isChallengeSuccess } from '@/features/challenge-history'
import {
  SegmentMarkButton,
  SegmentPreviewButton,
  SegmentTable,
  useSegmentPreview,
} from '@/features/song-discovery'
import { useSegmentMarks } from '@/features/song-discovery/hooks'
import { searchSegments } from '@/features/song-discovery/model'
import type { SongMetadata } from '@/types'
import { formatTime } from '@/utils'
import { useState } from 'react'
import { ChallengeHistoryDetails } from './ChallengeHistoryDetails'
import { aggregateResults } from './model'
import { useProductResults } from './store'

type Props = {
  owner: string
  songs: SongMetadata[]
  marks: ReturnType<typeof useSegmentMarks>
  preview: ReturnType<typeof useSegmentPreview>
  onSelect: (song: SongMetadata) => void
}
export function ChallengeLibrary({ owner, songs, marks, preview, onSelect }: Props) {
  const { rows, error } = useProductResults(owner)
  const groups = aggregateResults(rows, isChallengeSuccess)
  const [tab, setTab] = useState('All Songs')
  const [filter, setFilter] = useState('All')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [historyFilter, setHistoryFilter] = useState('All')
  const [sort, setSort] = useState('Recently played')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [historyPage, setHistoryPage] = useState(1)
  const tried = new Set(groups.map((g) => g.latest.segment_id))
  const catalog = searchSegments(songs, query).filter(
    (s) =>
      filter === 'All' ||
      (filter === 'Favorites' && marks.marks.has(s.segment_id!)) ||
      (filter === 'Challenged' && tried.has(s.segment_id)) ||
      (filter === 'Not Tried' && !tried.has(s.segment_id)),
  )
  const pages = Math.max(1, Math.ceil(catalog.length / 10)),
    current = Math.min(page, pages)
  const history = groups
    .filter((g) => historyFilter === 'All' || g.status === historyFilter)
    .sort((a, b) =>
      sort === 'Best Completion'
        ? b.completion - a.completion
        : sort === 'Best Accuracy'
          ? (b.accuracy ?? -1) - (a.accuracy ?? -1)
          : sort === 'Most Attempts'
            ? b.rows.length - a.rows.length
            : b.latest.ended_at.localeCompare(a.latest.ended_at),
    )
  const hp = Math.min(historyPage, Math.max(1, Math.ceil(history.length / 10)))
  const meta = (g: (typeof groups)[number]) =>
    songs.find(
      (s) => s.segment_id === g.latest.segment_id && s.midi_sha256 === g.latest.midi_sha256,
    )
  const actions = (s: SongMetadata, label = 'Challenge') => (
    <div className="flex items-center gap-2">
      <SegmentMarkButton song={s} marks={marks} />
      <SegmentPreviewButton song={s} preview={preview} />
      <button className="rounded-lg bg-amber-100 px-4 py-2 text-sm" onClick={() => onSelect(s)}>
        {label}
      </button>
    </div>
  )
  const card = (g: (typeof groups)[number], details = true) => {
    const s = meta(g)
    return (
      <article key={g.key} className="space-y-4 rounded-xl border border-amber-100 bg-white p-5">
        <div className="flex flex-wrap justify-between gap-2">
          <div>
            <h3 className="font-medium">{s?.title ?? 'Unavailable segment'}</h3>
            <p className="text-sm text-gray-500">
              {s?.composer} {s && `· ${formatTime(s.duration)}`}
            </p>
          </div>
          <span
            className={
              g.status === 'Succeeded'
                ? 'text-green-700'
                : g.status === 'Completed'
                  ? 'text-slate-600'
                  : 'text-amber-700'
            }
          >
            {g.status}
          </span>
        </div>
        <div className="flex justify-between gap-4 text-sm">
          <span>Best Completion · {(g.completion * 100).toFixed(1)}%</span>
          {details && (
            <span>
              Best Accuracy · {g.accuracy === null ? '—' : `${(g.accuracy * 100).toFixed(1)}%`}
            </span>
          )}
        </div>
        <progress
          className="h-2 w-full accent-amber-500"
          max={1}
          value={g.completion}
          aria-label="Best Completion"
        />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm text-gray-500">
            {g.rows.length} attempts · Last played {new Date(g.latest.ended_at).toLocaleString()}
          </span>
          <div className="flex items-center gap-3">
            {details && (
              <button
                aria-expanded={expanded === g.key}
                onClick={() => setExpanded(expanded === g.key ? null : g.key)}
              >
                Details
              </button>
            )}
            {s ? (
              actions(s, g.status === 'Succeeded' ? 'Replay' : 'Try Again')
            ) : (
              <span className="text-sm text-gray-500">This version is unavailable.</span>
            )}
          </div>
        </div>
        {details && expanded === g.key && (
          <ChallengeHistoryDetails
            key={g.key}
            attempts={g.rows.map((r) => ({
              attemptId: r.attempt_id,
              endedAt: r.ended_at,
              completionPct: r.completion * 100,
              accuracyPct:
                r.frontend_note_accuracy === null ? null : r.frontend_note_accuracy * 100,
              isCompleted: r.is_completed,
              isSucceeded:
                r.is_completed &&
                r.frontend_note_accuracy !== null &&
                isChallengeSuccess(r.frontend_note_accuracy * 100),
            }))}
          />
        )}
      </article>
    )
  }
  const paginate = (p: number, count: number, total: number, change: (p: number) => void) => (
    <div className="my-3 flex justify-between gap-3 text-sm">
      <span>
        Page {p} of {count} · {total} results
      </span>
      <div className="flex gap-4">
        <button disabled={p <= 1} onClick={() => change(p - 1)}>
          Previous
        </button>
        <button disabled={p >= count} onClick={() => change(p + 1)}>
          Next
        </button>
      </div>
    </div>
  )
  return (
    <div className="space-y-6">
      <div
        role="tablist"
        aria-label="Challenge songs"
        className="flex gap-6 border-b border-amber-200"
      >
        {['All Songs', 'Challenged Songs'].map((t) => (
          <button
            role="tab"
            aria-selected={tab === t}
            key={t}
            className={`pb-3 ${tab === t ? 'border-b-2 border-amber-600 font-medium' : ''}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      {error && (
        <p role="status" className="text-sm text-gray-500">
          {error}
        </p>
      )}
      {tab === 'All Songs' ? (
        <>
          {groups[0] && (
            <section className="space-y-3">
              <h3 className="text-lg font-medium">Continue Practicing</h3>
              {card(groups[0], false)}
            </section>
          )}
          <section className="space-y-3">
            <h3 className="text-lg font-medium">Featured Songs</h3>
            <div className="grid gap-4 md:grid-cols-3">
              {songs.slice(0, 3).map((s) => (
                <article
                  key={s.id}
                  className="space-y-3 rounded-xl border border-amber-100 bg-white p-4"
                >
                  <h4 className="font-medium">{s.title}</h4>
                  <p className="text-sm text-gray-500">
                    {s.composer} · {formatTime(s.duration)}
                  </p>
                  {actions(s)}
                </article>
              ))}
            </div>
          </section>
          <section className="space-y-3">
            <h3 className="text-lg font-medium">All Songs</h3>
            <input
              aria-label="Search songs"
              placeholder="Search Song Title, Composer or Segment Title"
              className="w-full rounded-lg border border-amber-200 bg-white p-3"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setPage(1)
              }}
            />
            <div className="flex flex-wrap gap-4">
              {['All', 'Favorites', 'Not Tried', 'Challenged'].map((f) => (
                <button
                  key={f}
                  aria-pressed={filter === f}
                  className={filter === f ? 'font-semibold text-amber-800' : 'text-gray-600'}
                  onClick={() => {
                    setFilter(f)
                    setPage(1)
                  }}
                >
                  {f}
                  {f === 'Favorites' ? ` (${marks.marks.size})` : ''}
                </button>
              ))}
            </div>
            <div className="overflow-hidden rounded-xl border border-amber-100 bg-white">
              <SegmentTable
                songs={catalog.slice((current - 1) * 10, current * 10)}
                onSelect={onSelect}
                marks={marks}
                preview={preview}
              />
              {!catalog.length && (
                <p className="p-6 text-center text-gray-500">No matching songs.</p>
              )}
            </div>
            {paginate(current, pages, catalog.length, setPage)}
          </section>
        </>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3">
            {[
              [groups.length, 'Challenged'],
              [groups.filter((g) => g.status === 'Succeeded').length, 'Succeeded'],
              [rows.filter((r) => r.is_completed).length, 'Full Challenges'],
            ].map(([n, label]) => (
              <div key={label} className="rounded-xl border border-amber-100 bg-white p-4">
                <strong className="block text-xl">{n}</strong>
                <span className="text-sm text-gray-500">{label}</span>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap justify-between gap-3">
            <div className="flex flex-wrap gap-3">
              {['All', 'In Progress', 'Completed', 'Succeeded'].map((f) => (
                <button
                  key={f}
                  aria-pressed={historyFilter === f}
                  onClick={() => {
                    setHistoryFilter(f)
                    setHistoryPage(1)
                  }}
                >
                  {f}
                </button>
              ))}
            </div>
            <select
              aria-label="Sort history"
              value={sort}
              onChange={(e) => {
                setSort(e.target.value)
                setHistoryPage(1)
              }}
            >
              {['Recently played', 'Best Completion', 'Best Accuracy', 'Most Attempts'].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </div>
          {history.slice((hp - 1) * 10, hp * 10).map((g) => card(g))}
          {!history.length && (
            <p className="py-8 text-gray-500">No challenges yet. Choose a song from All Songs.</p>
          )}
          {paginate(
            hp,
            Math.max(1, Math.ceil(history.length / 10)),
            history.length,
            setHistoryPage,
          )}
        </>
      )}
    </div>
  )
}
