import { AppBar, MarketingFooter, Sizer } from '@/components'
import { useRequireAuth } from '@/features/auth'
import {
  getBestAccuracyPerSong,
  isChallengeSuccess,
  listChallengeRecordings,
} from '@/features/challenge-history'
import { useSongManifest } from '@/features/data/library'
import { getKey } from '@/utils'
import type { SongMetadata, SongSource } from '@/types'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

type ChallengedItem = {
  source: SongSource
  id: string
  title: string
  bestAccuracy: number
  success: boolean
}

export default function ChallengeSongsPage() {
  const { user, loading: authLoading } = useRequireAuth('challenge-songs')
  const { userId: routeUserId } = useParams<{ userId: string }>()
  const navigate = useNavigate()
  const songs = useSongManifest()
  const [recordings, setRecordings] = useState<ChallengedItem[]>([])
  const [loading, setLoading] = useState(true)

  const songMap = useMemo(() => {
    const m = new Map<string, SongMetadata>()
    songs.forEach((s) => m.set(getKey(s.id, s.source), s))
    return m
  }, [songs])

  useEffect(() => {
    if (!user?.id) return
    setLoading(true)
    listChallengeRecordings().then((result) => {
      setLoading(false)
      if ('error' in result) return
      const bestMap = getBestAccuracyPerSong(result.data)
      const list: ChallengedItem[] = []
      bestMap.forEach((v, key) => {
        const [source, id] = key.split('/') as [SongSource, string]
        const meta = songMap.get(key)
        list.push({
          source,
          id: decodeURIComponent(id),
          title: meta?.title ?? v.songTitle ?? id,
          bestAccuracy: v.bestAccuracy,
          success: isChallengeSuccess(v.bestAccuracy),
        })
      })
      list.sort((a, b) => b.bestAccuracy - a.bestAccuracy)
      setRecordings(list)
    })
  }, [user?.id, songMap])

  const challengedKeys = useMemo(
    () => new Set(recordings.map((r) => getKey(r.id, r.source))),
    [recordings],
  )
  const recommended = useMemo(
    () => songs.filter((s) => !challengedKeys.has(getKey(s.id, s.source))),
    [songs, challengedKeys],
  )

  if (authLoading || !user) {
    return (
      <>
        <AppBar />
        <div className="flex min-h-screen items-center justify-center bg-paper bg-amber-50/70">
          <p className="text-gray-500">Loading…</p>
        </div>
      </>
    )
  }

  const goChallenge = (source: SongSource, id: string) => {
    navigate(`/challenge?source=${encodeURIComponent(source)}&id=${encodeURIComponent(id)}`)
  }

  return (
    <>
      <title>Take a challenge</title>
      <div className="flex min-h-screen flex-col bg-paper bg-amber-50/70">
        <AppBar />
        <div className="mx-auto flex min-h-0 w-full max-w-(--breakpoint-lg) flex-1 flex-col p-6">
          <h2 className="text-2xl font-semibold text-gray-900">Take a challenge</h2>
          <Sizer height={4} />
          <p className="text-sm text-gray-600">
            Challenge a song to the end. With accuracy ≥ 90% seen as success.
          </p>
          <Sizer height={24} />

          {/* 挑战过的 */}
          <section>
            <h3 className="text-lg font-medium text-gray-900">Challenged songs</h3>
            <Sizer height={8} />
            {loading ? (
              <div className="rounded-xl border border-amber-100 bg-white py-12 text-center text-sm text-gray-400">
                Loading…
              </div>
            ) : recordings.length === 0 ? (
              <div className="rounded-xl border border-amber-100 bg-white py-12 text-center text-sm text-gray-500">
                Haven't challenged any song, pick one from "Recommended Songs" and try.
              </div>
            ) : (
              <div className="overflow-hidden rounded-xl border border-amber-100 bg-white shadow-sm">
                <table className="w-full table-fixed text-sm">
                  <thead>
                    <tr className="border-b border-amber-100 bg-amber-50/50">
                      <th className="px-4 py-2.5 text-left font-semibold text-gray-500">Title</th>
                      <th className="w-28 px-4 py-2.5 text-right font-semibold text-gray-500">
                        Best Accuracy
                      </th>
                      <th className="w-24 px-4 py-2.5 text-right font-semibold text-gray-500">
                        Status
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {recordings.map((r) => (
                      <tr
                        key={getKey(r.id, r.source)}
                        onClick={() => goChallenge(r.source, r.id)}
                        className="cursor-pointer border-b border-amber-50 transition-colors hover:bg-amber-50/50"
                      >
                        <td className="truncate px-4 py-2.5 font-medium text-gray-900">
                          {r.title}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-gray-600">
                          {r.bestAccuracy.toFixed(1)}%
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          {r.success ? (
                            <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
                              Success
                            </span>
                          ) : (
                            <span className="text-gray-400">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <Sizer height={32} />

          {/* 推荐你的 */}
          <section>
            <h3 className="text-lg font-medium text-gray-900">Recommended Songs</h3>
            <Sizer height={8} />
            {recommended.length === 0 ? (
              <div className="rounded-xl border border-amber-100 bg-white py-12 text-center text-sm text-gray-500">
                No recommended songs, please go to
                <Link to="/songs" className="ml-1 font-medium text-gray-900 underline">
                  "Practice a song"
                </Link>
                to have a practice.
              </div>
            ) : (
              <div className="overflow-hidden rounded-xl border border-amber-100 bg-white shadow-sm">
                <table className="w-full table-fixed text-sm">
                  <thead>
                    <tr className="border-b border-amber-100 bg-amber-50/50">
                      <th className="px-4 py-2.5 text-left font-semibold text-gray-500">Title</th>
                      <th className="w-24 px-4 py-2.5 text-right font-semibold text-gray-500">
                        Length
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {recommended.map((s) => (
                      <tr
                        key={getKey(s.id, s.source)}
                        onClick={() => goChallenge(s.source, s.id)}
                        className="cursor-pointer border-b border-amber-50 transition-colors hover:bg-amber-50/50"
                      >
                        <td className="truncate px-4 py-2.5 font-medium text-gray-900">
                          {s.title}
                        </td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-gray-500">
                          {formatTime(Number(s.duration))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <Sizer height={24} />
          <p className="text-sm text-gray-500">
            <Link to="/songs" className="font-medium text-gray-900 underline hover:no-underline">
              Practice a song
            </Link>
            {' · '}
            <Link to="/" className="font-medium text-gray-900 underline hover:no-underline">
              Home
            </Link>
          </p>
        </div>
        <MarketingFooter />
      </div>
    </>
  )
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
