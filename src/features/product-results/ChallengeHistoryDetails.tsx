import { ChevronLeft, ChevronRight, Trophy } from 'lucide-react'
import { useState } from 'react'

/** Product outcomes, not alignment results. All percentages use 0–100; null is unknown. */
export type ProductAttempt = {
  attemptId: string
  endedAt: string
  completionPct: number | null
  accuracyPct: number | null
  isCompleted: boolean | null
  isSucceeded: boolean | null
}

const PAGE_SIZE = 10
const pct = (value: number | null) => (value === null ? '—' : `${value.toFixed(1)}%`)
const status = (a: ProductAttempt) =>
  a.isSucceeded === true
    ? 'Succeeded'
    : a.isCompleted === true
      ? 'Completed'
      : a.isCompleted === false
        ? 'Incomplete'
        : 'Unknown'
const tone = (a: ProductAttempt) =>
  a.isSucceeded === true
    ? 'text-green-700'
    : a.isCompleted === true
      ? 'text-slate-600'
      : a.isCompleted === false
        ? 'text-amber-700'
        : 'text-gray-500'
const bar = (a: ProductAttempt) =>
  a.isSucceeded === true
    ? 'bg-green-600'
    : a.isCompleted === true
      ? 'bg-slate-400'
      : 'bg-amber-500/70'

function PageChart({ rows }: { rows: { attempt: ProductAttempt; number: number }[] }) {
  const chronological = [...rows].reverse()
  const x = (i: number) =>
    chronological.length === 1 ? 342 : 46 + (i * 592) / Math.max(1, chronological.length - 1)
  const y = (v: number) => 150 - 1.25 * v
  const series = [
    { key: 'completionPct' as const, color: '#c4a15b', dash: undefined, label: 'Completion' },
    { key: 'accuracyPct' as const, color: '#329568', dash: '5 3', label: 'Accuracy' },
  ]
  return (
    <>
      <svg
        viewBox="0 0 680 184"
        className="block w-full"
        role="img"
        aria-label={`Completion and accuracy for attempts ${chronological[0]?.number} through ${chronological.at(-1)?.number}`}
      >
        {[0, 50, 90, 100].map((tick) => (
          <g key={tick}>
            <line
              x1={40}
              x2={650}
              y1={y(tick)}
              y2={y(tick)}
              stroke={tick === 90 ? '#8ab69a' : '#e7e5e4'}
              strokeDasharray={tick === 90 ? '4 4' : undefined}
            />
            <text x={34} y={y(tick) + 4} textAnchor="end" fontSize={11} fill="#78716c">
              {tick}%
            </text>
          </g>
        ))}
        {series.map((s) => (
          <g key={s.key}>
            {chronological.map(({ attempt, number }, i) => {
              const v = attempt[s.key]
              if (v === null) return null
              const previous = chronological[i - 1]?.attempt[s.key]
              return (
                <g key={attempt.attemptId}>
                  {i > 0 && previous !== null && previous !== undefined && (
                    <line
                      x1={x(i - 1)}
                      y1={y(previous)}
                      x2={x(i)}
                      y2={y(v)}
                      stroke={s.color}
                      strokeWidth={2.5}
                      strokeDasharray={s.dash}
                    />
                  )}
                  <circle cx={x(i)} cy={y(v)} r={3.5} fill={s.color} stroke="white">
                    <title>{`Attempt #${number}: ${s.label} ${pct(v)}${s.key === 'accuracyPct' && !attempt.isCompleted ? ' (partial)' : ''}`}</title>
                  </circle>
                </g>
              )
            })}
          </g>
        ))}
        {chronological.map(({ attempt, number }, i) => (
          <text
            key={attempt.attemptId}
            x={x(i)}
            y={173}
            textAnchor="middle"
            fontSize={12}
            fill="#78716c"
          >
            #{number}
          </text>
        ))}
      </svg>
      <div className="flex flex-wrap justify-center gap-4 text-xs text-gray-500">
        <span>
          <span className="mr-1 inline-block size-2 rounded-full bg-[#c4a15b]" />
          Completion
        </span>
        <span>
          <span className="mr-1 inline-block size-2 rounded-full bg-[#329568]" />
          Accuracy (partial or full)
        </span>
      </div>
    </>
  )
}

/** Mount only inside the expanded song card. Parent should key by segment identity.
 * This version takes the complete, deduplicated array for ONE segment, not a truncated Supabase query.
 * For large histories, move ordering, count, and slicing to the product history endpoint.
 */
export function ChallengeHistoryDetails({ attempts }: { attempts: ProductAttempt[] }) {
  const [requestedPage, setPage] = useState(1)
  const sorted = [...attempts].sort(
    (a, b) =>
      Date.parse(b.endedAt) - Date.parse(a.endedAt) || b.attemptId.localeCompare(a.attemptId),
  )
  const total = sorted.length
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.min(requestedPage, pages)
  const offset = (page - 1) * PAGE_SIZE
  const rows = sorted
    .slice(offset, offset + PAGE_SIZE)
    .map((attempt, i) => ({ attempt, number: total - offset - i }))
  const fullAccuracies = sorted
    .filter((a) => a.isCompleted === true && a.accuracyPct !== null)
    .map((a) => a.accuracyPct!)
  const best = fullAccuracies.length ? Math.max(...fullAccuracies) : null
  const bestId =
    best === null
      ? null
      : sorted.find((a) => a.isCompleted === true && a.accuracyPct === best)?.attemptId
  const pagination = (position: string) => (
    <nav
      aria-label={`Challenge history pagination ${position}`}
      className="flex flex-wrap items-center justify-between gap-2 text-sm"
    >
      <span className="text-xs text-gray-500" aria-live="polite">
        {total
          ? `Showing ${offset + 1}–${offset + rows.length} of ${total} · Page ${page} of ${pages}`
          : 'No attempts'}
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setPage(page - 1)}
          disabled={page <= 1}
          className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-white px-3 py-2 disabled:opacity-40"
        >
          <ChevronLeft size={14} />
          Newer
        </button>
        <button
          type="button"
          onClick={() => setPage(page + 1)}
          disabled={page >= pages}
          className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-white px-3 py-2 disabled:opacity-40"
        >
          Older
          <ChevronRight size={14} />
        </button>
      </div>
    </nav>
  )
  return (
    <section
      className="space-y-4 rounded-xl border border-amber-100 bg-amber-50/50 p-3 sm:p-4"
      aria-label="Challenge history"
    >
      <div className="flex items-center justify-between gap-2">
        <h4 className="font-medium text-gray-900">Challenge History</h4>
        <span className="text-xs text-gray-500">{total} attempts</span>
      </div>
      {!total ? (
        <p className="text-sm text-gray-500">No challenge history yet.</p>
      ) : (
        <>
          {pagination('top')}
          <PageChart rows={rows} />
          <div className="space-y-2">
            {rows.map(({ attempt: a, number }) => (
              <article
                key={a.attemptId}
                className="space-y-3 rounded-lg border border-amber-100 bg-white p-3"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h5 className="text-sm font-medium">Attempt #{number}</h5>
                    <p className="text-xs text-gray-500">{new Date(a.endedAt).toLocaleString()}</p>
                  </div>
                  <span className={`text-xs font-medium ${tone(a)}`}>{status(a)}</span>
                </div>
                <div className="grid grid-cols-[2fr_1fr] items-center gap-4">
                  <div className="space-y-2">
                    <div className="flex justify-between gap-1 text-xs text-gray-500">
                      <span>Completion</span>
                      <span>{pct(a.completionPct)}</span>
                    </div>
                    {a.completionPct !== null && (
                      <div
                        role="progressbar"
                        aria-label="Completion"
                        aria-valuenow={a.completionPct}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        className="h-1.5 overflow-hidden rounded-full bg-amber-100"
                      >
                        <div
                          className={`h-full rounded-full ${bar(a)}`}
                          style={{ width: `${Math.max(0, Math.min(100, a.completionPct))}%` }}
                        />
                      </div>
                    )}
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">
                      {a.isCompleted === false ? 'Partial Accuracy' : 'Accuracy'}
                    </p>
                    <p className="text-lg font-semibold tabular-nums">{pct(a.accuracyPct)}</p>
                  </div>
                </div>
                {a.attemptId === bestId && (
                  <p className="flex items-center gap-1 text-xs font-medium text-amber-700">
                    <Trophy size={14} />
                    Personal best
                  </p>
                )}
              </article>
            ))}
          </div>
          {pagination('bottom')}
        </>
      )}
    </section>
  )
}
