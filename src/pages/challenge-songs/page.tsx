import { AppBar, MarketingFooter, Sizer } from '@/components'
import { useRequireAuth } from '@/features/auth'
import { useSongManifest } from '@/features/data/library'
import { ChallengeLibrary } from '@/features/product-results/ChallengeLibrary'
import { useSegmentPreview } from '@/features/song-discovery'
import { useSegmentMarks } from '@/features/song-discovery/hooks'
import { SongPreviewModal } from '@/features/SongPreview'
import type { SongMetadata } from '@/types'
import { useState } from 'react'
import { Link } from 'react-router'

export default function ChallengeSongsPage() {
  const { user, loading: authLoading } = useRequireAuth('challenge-songs')
  const preview = useSegmentPreview()
  const marks = useSegmentMarks()
  const songs = useSongManifest()
  const [selectedForPreview, setSelectedForPreview] = useState<SongMetadata | null>(null)

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

          <ChallengeLibrary
            key={user.id}
            owner={user.id}
            songs={songs.filter((s) => s.source === 'builtin')}
            marks={marks}
            preview={preview}
            onSelect={openPreview}
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
