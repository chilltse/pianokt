import { AppBar, MarketingFooter, Modal, Sizer } from '@/components'
import { useOptionalAuth } from '@/features/auth'
import { useCatalogStatus, useSongManifest } from '@/features/data/library'
import { prefetchSongs } from '@/features/data/prefetch'
import { isInitializedAtom } from '@/features/persist/persistence'
import { SongDiscovery, useSegmentPreview } from '@/features/song-discovery'
import { SongPreviewModal } from '@/features/SongPreview'
import { useEventListener } from '@/hooks'
import { ChevronDown, FolderOpen } from '@/icons'
import { SongMetadata } from '@/types'
import clsx from 'clsx'
import { useAtomValue } from 'jotai'
import * as React from 'react'
import { useState } from 'react'
import ManageFoldersForm from './components/AddFolderForm'

// TODO: after an upload, scroll to the newly uploaded song / make it focused.
export default function SelectSongPage() {
  const catalogStatus = useCatalogStatus()
  let songs: SongMetadata[] = useSongManifest()
  const isInitialized = useAtomValue(isInitializedAtom)
  useOptionalAuth()
  const [isUploadFormOpen, setUploadForm] = useState<boolean>(false)
  const [selectedSongMeta, setSelectedSongMeta] = useState<SongMetadata | null>(null)
  const preview = useSegmentPreview()
  const openPreview = (song: SongMetadata) => {
    preview.stop()
    setSelectedSongMeta(song)
  }
  const [prefetch, setPrefetch] = useState(false)
  React.useEffect(() => {
    if (!prefetch || !catalogStatus.data) return
    const controller = new AbortController()
    prefetchSongs(catalogStatus.data, controller.signal)
    return () => controller.abort()
  }, [prefetch, catalogStatus.data])

  useEventListener<KeyboardEvent>('keydown', (event) => {
    if (event.key === 'Escape') {
      setUploadForm(false)
    }
  })

  const handleAddNew = (e: any) => {
    setUploadForm(true)
    e.stopPropagation()
  }

  const handleCloseAddNew = () => {
    setUploadForm(false)
  }

  return (
    <>
      <title>Practice a song</title>
      <SongPreviewModal
        show={!!selectedSongMeta}
        mode="play"
        songMeta={selectedSongMeta ?? undefined}
        onClose={() => {
          setSelectedSongMeta(null)
        }}
      />
      <Modal show={isUploadFormOpen} onClose={handleCloseAddNew} className="w-[min(100vw,500px)]">
        <ManageFoldersForm onClose={handleCloseAddNew} />
      </Modal>
      <div className="bg-paper flex min-h-screen w-full flex-col bg-amber-50/70">
        <div className="shrink-0">
          <AppBar />
        </div>
        <div className="mx-auto flex min-h-0 w-full max-w-(--breakpoint-lg) flex-1 flex-col p-6">
          <h2 className="text-2xl font-semibold text-gray-900">Practice a song</h2>
          <Sizer height={4} />
          <h3 className="text-sm text-gray-600">
            Select a song, choose your settings, and have fun practicing!
          </h3>
          <Sizer height={16} />
          <div className="flex items-center gap-4">
            <button
              className={clsx(
                'cursor-pointer flex-nowrap whitespace-nowrap',
                'inline-flex items-center gap-2 rounded-md border border-amber-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm',
                'transition-colors hover:bg-amber-50',
              )}
              onClick={handleAddNew}
            >
              <FolderOpen width={16} height={16} />
              Folders
              <ChevronDown width={16} height={16} />
            </button>
          </div>
          <label className="text-sm">
            <input
              type="checkbox"
              checked={prefetch}
              onChange={(e) => setPrefetch(e.target.checked)}
            />{' '}
            空闲时缓存官方 MIDI
          </label>
          <Sizer height={20} />
          {isInitialized ? (
            <SongDiscovery songs={songs} onSelect={openPreview} preview={preview} />
          ) : (
            <p>Loading…</p>
          )}
        </div>
        <div className="shrink-0">
          <MarketingFooter />
        </div>
      </div>
    </>
  )
}
