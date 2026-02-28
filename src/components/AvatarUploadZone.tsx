import { uploadAvatar, validateAvatarFile } from '@/features/auth'
import clsx from 'clsx'
import { useRef, useState } from 'react'

const ACCEPT = 'image/jpeg,image/png,image/webp,image/gif'
const MAX_SIZE_MB = 2

type Props = {
  currentUrl: string | null
  onUploadSuccess: (url: string) => void
  disabled?: boolean
  className?: string
}

export function AvatarUploadZone({
  currentUrl,
  onUploadSuccess,
  disabled,
  className,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleFile(file: File | null) {
    if (!file) return
    setError(null)
    const validation = validateAvatarFile(file)
    if (!validation.ok) {
      setError(validation.error)
      return
    }
    setUploading(true)
    const result = await uploadAvatar(file)
    setUploading(false)
    if (result.error) {
      setError(result.error)
      return
    }
    onUploadSuccess(result.url)
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setIsDragging(false)
    if (disabled || uploading) return
    const file = e.dataTransfer.files?.[0]
    if (file) handleFile(file)
  }

  function onDragOver(e: React.DragEvent) {
    e.preventDefault()
    e.stopPropagation()
    if (disabled || uploading) return
    setIsDragging(true)
  }

  function onDragLeave(e: React.DragEvent) {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
  }

  function onClick() {
    if (disabled || uploading) return
    inputRef.current?.click()
  }

  function onInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
    e.target.value = ''
  }

  return (
    <div className={clsx('flex flex-col gap-3', className)}>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        aria-hidden
        onChange={onInputChange}
      />
      <div
        role="button"
        tabIndex={0}
        onClick={onClick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onClick()
          }
        }}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        aria-label="Upload avatar"
        className={clsx(
          'flex cursor-pointer items-center gap-4 rounded-xl border-2 border-dashed p-4 transition-colors',
          disabled || uploading
            ? 'cursor-not-allowed border-gray-200 bg-gray-50'
            : isDragging
              ? 'border-amber-400 bg-amber-50/80'
              : 'border-gray-300 bg-gray-50/80 hover:border-amber-300 hover:bg-amber-50/50',
          className,
        )}
      >
        <div className="relative flex h-20 w-20 shrink-0 overflow-hidden rounded-full bg-gray-200 ring-2 ring-white">
          {uploading ? (
            <div className="flex h-full w-full items-center justify-center text-xs text-gray-500">
              …
            </div>
          ) : currentUrl ? (
            <img
              src={currentUrl}
              alt="Avatar"
              className="h-full w-full object-cover"
              onError={(e) => (e.currentTarget.style.display = 'none')}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-2xl text-gray-400">
              +
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1 text-left">
          <p className="text-sm font-medium text-gray-700">
            {uploading ? 'Uploading…' : 'Drop image here or click to upload'}
          </p>
          <p className="mt-0.5 text-xs text-gray-500">
            JPEG, PNG, WebP or GIF, max {MAX_SIZE_MB}MB
          </p>
        </div>
      </div>
      {error && (
        <p className="text-sm text-red-600" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
