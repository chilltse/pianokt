import { AppBar } from '@/components'
import { useRequireAuth } from '@/features/auth'
import {
  getChallengeRecordingDownloadUrl,
  listChallengeRecordings,
  type ChallengeRecordingRow,
} from '@/features/challenge-history'
import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'

function formatDate(iso: string) {
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDuration(sec: number) {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function RecordingsPage() {
  const { user, loading: authLoading } = useRequireAuth('recordings')
  const { userId: routeUserId } = useParams<{ userId: string }>()
  const [rows, setRows] = useState<ChallengeRecordingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!user?.id) return
    let cancelled = false
    setLoading(true)
    setError(null)
    listChallengeRecordings().then((result) => {
      if (cancelled) return
      setLoading(false)
      if ('error' in result) setError(result.error)
      else setRows(result.data)
    })
    return () => {
      cancelled = true
    }
  }, [user?.id])

  async function handleDownload(row: ChallengeRecordingRow) {
    const url = await getChallengeRecordingDownloadUrl(row.midi_storage_path)
    if (url) {
      const a = document.createElement('a')
      a.href = url
      a.download = `${row.song_title || 'recording'}.mid`
      a.click()
    }
  }

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

  return (
    <>
      <AppBar />
      <div className="bg-paper bg-amber-50/70 min-h-screen">
        <div className="mx-auto max-w-2xl px-4 py-8">
        <h1 className="text-2xl font-semibold text-gray-900">My challenge recordings</h1>
        <p className="mt-1 text-sm text-gray-600">
          Recordings from Challenge mode are saved here when you’re logged in.
        </p>
        {loading ? (
          <p className="mt-6 text-gray-500">Loading…</p>
        ) : error ? (
          <p className="mt-6 text-red-600">{error}</p>
        ) : rows.length === 0 ? (
          <>
            <p className="mt-6 text-gray-500">No recordings yet. Complete a challenge while logged in to save one.</p>
            <Link to="/songs" className="mt-4 inline-block text-stone-600 hover:text-stone-800 hover:underline">
              Go to songs
            </Link>
          </>
        ) : (
          <ul className="mt-6 space-y-3">
            {rows.map((row) => (
              <li
                key={row.id}
                className="flex items-center justify-between rounded-lg border border-amber-100 bg-white px-4 py-3 shadow-sm"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-gray-900">
                    {row.song_title || `${row.song_source} / ${row.song_id.slice(0, 12)}…`}
                  </p>
                  <p className="text-xs text-gray-500">
                    {formatDate(row.created_at)} · {formatDuration(Number(row.duration_sec))}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleDownload(row)}
                  className="ml-4 flex items-center gap-1 rounded-md border border-amber-200 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-amber-50"
                >
                  <Download className="h-4 w-4" />
                  Download
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-8">
          <Link to="/" className="text-sm text-stone-600 hover:text-stone-800 hover:underline">
            Back to home
          </Link>
        </p>
        </div>
      </div>
    </>
  )
}
