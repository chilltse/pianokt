import { AppBar, MarketingFooter, Sizer } from '@/components'
import { useRequireAuth } from '@/features/auth'
import {
  getBestAccuracyPerSong,
  isChallengeSuccess,
  listChallengeRecordings,
} from '@/features/challenge-history'
import { useSongManifest } from '@/features/data/library'
import {
  SegmentMarkButton,
  SegmentPreviewButton,
  SongDiscovery,
  useSegmentPreview,
} from '@/features/song-discovery'
import { useSegmentMarks } from '@/features/song-discovery/hooks'
import { SongPreviewModal } from '@/features/SongPreview'
import type { SongMetadata, SongSource } from '@/types'
import { getKey } from '@/utils'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'

type ChallengedItem = {
  source: SongSource
  id: string
  title: string
  bestAccuracy: number
  success: boolean
  official?: SongMetadata
}

export default function ChallengeSongsPage() {
  const { user, loading: authLoading } = useRequireAuth('challenge-songs')
  const preview = useSegmentPreview()
  const marks = useSegmentMarks()
  const [historyError, setHistoryError] = useState('')
  const songs = useSongManifest()
  const [recordings, setRecordings] = useState<ChallengedItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedForPreview, setSelectedForPreview] = useState<SongMetadata | null>(null)

  const songMap = useMemo(() => {
    const m = new Map<string, SongMetadata>()
    songs.forEach((s) => {
      m.set(getKey(s.id, s.source), s)
    })
    return m
  }, [songs])

  useEffect(() => {
    if (!user?.id) return
    let active = true
    setLoading(true)
    setRecordings([])
    setHistoryError('')
    listChallengeRecordings()
      .then((result) => {
        if (!active) return
        setLoading(false)
        if ('error' in result) {
          setHistoryError('Unable to load challenge history. Please refresh to retry.')
          return
        }
        const bestMap = getBestAccuracyPerSong(result.data)
        const officialIds = new Set(
          result.data
            .filter((r) => r.song_source === 'builtin' && r.segment_id)
            .map((r) => r.segment_id),
        )
        const list: ChallengedItem[] = []
        bestMap.forEach((v, key) => {
          const separator = key.indexOf('/')
          const source = key.slice(0, separator) as SongSource
          const id = key.slice(separator + 1)
          const meta =
            source !== 'builtin' || officialIds.has(id)
              ? songMap.get(getKey(id, source))
              : undefined
          list.push({
            source,
            id,
            official:
              source === 'builtin' && officialIds.has(id) && meta?.segment_id === id
                ? meta
                : undefined,
            title: meta?.title ?? v.songTitle ?? id,
            bestAccuracy: v.bestAccuracy,
            success: isChallengeSuccess(v.bestAccuracy),
          })
        })
        list.sort((a, b) => b.bestAccuracy - a.bestAccuracy)
        setRecordings(list)
      })
      .catch(() => {
        if (active) {
          setLoading(false)
          setHistoryError('Unable to load challenge history. Please refresh to retry.')
        }
      })
    return () => {
      active = false
    }
  }, [user?.id, songMap])

  if (authLoading || !user) {
    return (
      <>
        <AppBar />
        <div className="bg-paper flex min-h-screen items-center justify-center bg-amber-50/70">
          <p className="text-gray-500">Loading…</p>
        </div>
      </>
    )
  }

  const openPreview = (meta: SongMetadata) => {
    preview.stop()
    setSelectedForPreview(meta)
  }

  return (
    <>
      <title>Take a challenge</title>
      <SongPreviewModal
        show={!!selectedForPreview}
        songMeta={selectedForPreview ?? undefined}
        onClose={() => setSelectedForPreview(null)}
        mode="challenge"
      />
      <div className="bg-paper flex min-h-screen flex-col bg-amber-50/70">
        <AppBar />
        <div className="mx-auto flex min-h-0 w-full max-w-(--breakpoint-lg) flex-1 flex-col p-6">
          <div className="flex flex-wrap items-center justify-between gap-6">
            <div className="min-w-0 flex-1">
              <h2 className="text-2xl font-semibold text-gray-900">Take a challenge</h2>
              <Sizer height={4} />
              <p className="text-sm text-gray-600">
                Challenge a song to the end. With accuracy ≥ 90% seen as success.
              </p>
            </div>
            <img
              src="/images/challenge-anime.png"
              alt="Challenge"
              className="h-32 w-auto shrink-0 rounded-xl object-contain sm:h-36 md:h-40"
            />
          </div>
          <Sizer height={24} />

          {/* 挑战过的 */}
          <section>
            <h3 className="text-lg font-medium text-gray-900">
              Challenged Songs{' '}
              <span className="text-sm font-normal text-gray-500">
                (Alignment and scoring may take up to 5 minutes.)
              </span>
            </h3>
            {historyError && <p role="alert">{historyError}</p>}
            <Sizer height={8} />
            {loading ? (
              <div className="rounded-xl border border-amber-100 bg-white py-12 text-center text-sm text-gray-400">
                Loading…
              </div>
            ) : recordings.length === 0 ? (
              <div className="rounded-xl border border-amber-100 bg-white py-12 text-center text-sm text-gray-500">
                Haven't challenged any song, pick one from "All Songs" and try.
              </div>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-amber-100 bg-white shadow-sm">
                <table className="w-full min-w-[560px] table-fixed text-sm">
                  <thead>
                    <tr className="border-b border-amber-100 bg-amber-50/50">
                      <th className="px-4 py-2.5 text-left font-semibold text-gray-500">Title</th>
                      <th className="w-28 px-4 py-2.5 text-right font-semibold text-gray-500">
                        Best Accuracy
                      </th>
                      <th className="w-24 px-4 py-2.5 text-right font-semibold text-gray-500">
                        Status
                      </th>
                      <th className="w-12">
                        <span className="sr-only">Mark</span>
                      </th>
                      <th className="w-12">
                        <span className="sr-only">Preview</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {recordings.map((r) => (
                      <tr
                        key={getKey(r.id, r.source)}
                        onClick={() => {
                          if (r.source === 'builtin' && !r.official) return
                          openPreview(
                            r.official ??
                              songMap.get(getKey(r.id, r.source)) ?? {
                                id: r.id,
                                source: r.source,
                                title: r.title,
                                file: '',
                                difficulty: 0,
                                duration: 0,
                              },
                          )
                        }}
                        className="cursor-pointer border-b border-amber-50 transition-colors hover:bg-amber-50/50"
                      >
                        <td className="truncate px-4 py-2.5 font-medium text-gray-900">
                          {r.title}
                        </td>
                        <td className="px-4 py-2.5 text-right text-gray-600 tabular-nums">
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
                        <td>
                          {r.official && <SegmentMarkButton song={r.official} marks={marks} />}
                        </td>
                        <td>
                          {r.official && (
                            <SegmentPreviewButton song={r.official} preview={preview} />
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

          <SongDiscovery
            marks={marks}
            songs={songs.filter((s) => s.source === 'builtin')}
            onSelect={openPreview}
            preview={preview}
          />

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
