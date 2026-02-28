import { useOptionalAuth } from '@/features/auth'
import { ModalOverlay, Modal as RACModal } from 'react-aria-components'
import { useNavigate } from 'react-router'
import { tv } from 'tailwind-variants'

const overlayStyles = tv({
  base: 'fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4',
  variants: {
    isEntering: { true: 'animate-in fade-in duration-150' },
    isExiting: { true: 'animate-out fade-out duration-150' },
  },
})

const modalStyles = tv({
  base: 'w-full max-w-md overflow-hidden rounded-2xl bg-white shadow-2xl border border-amber-100 text-center',
  variants: {
    isEntering: { true: 'animate-in zoom-in-95 duration-200 ease-out' },
    isExiting: { true: 'animate-out zoom-out-95 duration-150 ease-in' },
  },
})

const DEFAULT_VIDEO_SUCCESS = '/videos/challenge-success.mp4'
const DEFAULT_VIDEO_COMPLETE = '/videos/challenge-fail.mp4'

export type ChallengeEndVariant = 'success' | 'complete'

type ChallengeSuccessModalProps = {
  show: boolean
  onClose: () => void
  /** success = 达标祝贺；complete = 未达标，显示 Challenge again / Back to Home */
  variant?: ChallengeEndVariant
  /** 达标时播放的视频 */
  videoSrcSuccess?: string
  /** 未达标时播放的视频 */
  videoSrcComplete?: string
}

export default function ChallengeSuccessModal({
  show,
  onClose,
  variant = 'success',
  videoSrcSuccess = DEFAULT_VIDEO_SUCCESS,
  videoSrcComplete = DEFAULT_VIDEO_COMPLETE,
}: ChallengeSuccessModalProps) {
  const navigate = useNavigate()
  const auth = useOptionalAuth()
  const user = auth?.user ?? null

  const handleChallengeAgain = () => {
    onClose()
    navigate(user ? `/challenge-songs/${user.id}` : '/challenge-songs')
  }

  const handleBackToHome = () => {
    onClose()
    navigate('/')
  }

  const isSuccess = variant === 'success'
  const videoSrc = isSuccess ? videoSrcSuccess : videoSrcComplete

  return (
    <ModalOverlay
      className={(p) => overlayStyles(p)}
      isOpen={show}
      isDismissable
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <RACModal className={(p) => modalStyles(p)} isDismissable>
        <div className="flex flex-col">
          <div className="relative aspect-video w-full bg-black/5">
            <video
              src={videoSrc}
              autoPlay
              loop
              muted
              playsInline
              className="h-full w-full object-contain"
              aria-hidden
            />
          </div>
          <div className="px-6 pb-6 pt-4">
            {isSuccess ? (
              <>
                <h2 className="text-xl font-semibold text-gray-900">Congratulations!</h2>
                <p className="mt-1 text-sm text-gray-600">You passed this challenge with flying colors.</p>
              </>
            ) : (
              <>
                <h2 className="text-xl font-semibold text-gray-900">Challenge complete</h2>
                <p className="mt-1 text-sm text-gray-600">Keep practicing to hit your target next time.</p>
              </>
            )}
            <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-center">
              <button
                type="button"
                onClick={handleChallengeAgain}
                className="rounded-lg bg-amber-500 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-amber-600"
              >
                {isSuccess ? 'Challenge another song' : 'Challenge again'}
              </button>
              <button
                type="button"
                onClick={handleBackToHome}
                className="rounded-lg border border-amber-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-amber-50"
              >
                Back to Home
              </button>
            </div>
          </div>
        </div>
      </RACModal>
    </ModalOverlay>
  )
}
