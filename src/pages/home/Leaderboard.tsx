import {
  fetchLeaderboard,
  type LeaderboardEntry,
  type LeaderboardSortBy,
} from '@/features/challenge-history'
import { supabase } from '@/features/auth/supabase'
import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'

const SORT_OPTIONS: { value: LeaderboardSortBy; label: string }[] = [
  { value: 'challenges', label: 'Challenges' },
  { value: 'accuracy', label: 'Accuracy' },
  { value: 'difficulty', label: 'Max difficulty' },
]

function formatValue(sortBy: LeaderboardSortBy, entry: LeaderboardEntry): string {
  switch (sortBy) {
    case 'challenges':
      return String(entry.challenge_count)
    case 'accuracy':
      return `${Number(entry.accuracy_avg).toFixed(1)}%`
    case 'difficulty':
      return Number(entry.max_difficulty).toFixed(1)
    default:
      return '—'
  }
}

const SHOW_LESS_COUNT = 5
const SHOW_MORE_COUNT = 10

export function Leaderboard() {
  const [sortBy, setSortBy] = useState<LeaderboardSortBy>('challenges')
  const [data, setData] = useState<LeaderboardEntry[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showMore, setShowMore] = useState(false) // default: show less (top 5)
  const sortByRef = useRef(sortBy)
  sortByRef.current = sortBy
  const displayLimit = showMore ? SHOW_MORE_COUNT : SHOW_LESS_COUNT
  const visibleData = data?.slice(0, displayLimit) ?? []

  const refetch = () => {
    fetchLeaderboard(sortByRef.current).then((result) => {
      if ('error' in result) setError(result.error)
      else setData(result.data)
    })
  }

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchLeaderboard(sortBy).then((result) => {
      setLoading(false)
      if ('error' in result) setError(result.error)
      else setData(result.data)
    })
  }, [sortBy])

  useEffect(() => {
    if (!supabase) return
    const channel = supabase
      .channel('leaderboard-sync')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'challenge_recordings' },
        () => refetch(),
      )
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'profiles' },
        () => refetch(),
      )
      .subscribe()
    return () => {
      supabase?.removeChannel(channel)
    }
  }, [])

  return (
    <div className="mx-auto w-full max-w-2xl">
      <div className="flex flex-col gap-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-lg font-semibold tracking-tight text-gray-900">Leaderboard</h3>
          <div className="flex rounded-lg border border-amber-100 bg-white p-0.5 shadow-sm">
            {SORT_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setSortBy(opt.value)}
                className={clsx(
                  'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                  sortBy === opt.value
                    ? 'bg-amber-100 text-gray-900'
                    : 'text-gray-500 hover:bg-amber-50/70 hover:text-gray-700',
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div className="overflow-hidden rounded-xl border border-amber-100 bg-white shadow-sm">
          {loading ? (
            <div className="flex items-center justify-center py-16 text-sm text-gray-400">
              Loading…
            </div>
          ) : error ? (
            <div className="py-12 text-center text-sm text-gray-500">
              Leaderboard unavailable. Complete challenges to appear here.
            </div>
          ) : !data?.length ? (
            <div className="py-12 text-center text-sm text-gray-500">
              No players yet. Be the first to complete a challenge.
            </div>
          ) : (
            <>
              <ul className="divide-y divide-amber-50">
                {visibleData.map((entry) => (
                  <li
                    key={entry.user_id}
                    className="flex items-center gap-4 px-4 py-3 transition-colors hover:bg-amber-50/50"
                  >
                    <span
                      className={clsx(
                        'w-8 shrink-0 text-right text-sm tabular-nums',
                        entry.rank <= 3
                          ? 'font-bold text-amber-600'
                          : 'font-medium text-gray-400',
                      )}
                    >
                      #{entry.rank}
                    </span>
                    <div className="relative h-9 w-9 shrink-0">
                      <div className="h-9 w-9 overflow-hidden rounded-full bg-amber-100">
                        {entry.avatar_url ? (
                          <img
                            src={entry.avatar_url}
                            alt=""
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <span className="flex h-full w-full items-center justify-center text-sm font-medium text-amber-700">
                            {(entry.display_name || '?').slice(0, 1).toUpperCase()}
                          </span>
                        )}
                      </div>
                      {entry.rank <= 3 && (
                        <span
                          className="absolute -top-2.5 left-1/2 -translate-x-1/2 text-base leading-none"
                          title={`Rank #${entry.rank}`}
                          aria-hidden
                        >
                          👑
                        </span>
                      )}
                    </div>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium text-gray-900">
                      {entry.display_name || 'Player'}
                    </span>
                    <span className="shrink-0 text-sm tabular-nums text-gray-600">
                      {formatValue(sortBy, entry)}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="flex justify-center border-t border-amber-50 py-3">
                {showMore ? (
                  <button
                    type="button"
                    onClick={() => setShowMore(false)}
                    className="cursor-pointer text-sm font-medium text-amber-700 hover:text-amber-800 transition-colors"
                  >
                    Show less
                  </button>
                ) : (
                  data.length > SHOW_LESS_COUNT && (
                    <button
                      type="button"
                      onClick={() => setShowMore(true)}
                      className="cursor-pointer text-sm font-medium text-amber-700 hover:text-amber-800 transition-colors"
                    >
                      Show more
                    </button>
                  )
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
