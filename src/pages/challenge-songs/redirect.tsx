import { AppBar } from '@/components'
import { useOptionalAuth } from '@/features/auth'
import { useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router'

export default function ChallengeSongsRedirect() {
  const auth = useOptionalAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const redirectParam = location.pathname + (location.search || '')

  useEffect(() => {
    if (auth === null || auth?.loading) return
    if (!auth?.user) {
      navigate(`/login?redirect=${encodeURIComponent(redirectParam)}`, { replace: true })
      return
    }
    navigate(`/challenge-songs/${auth.user.id}`, { replace: true })
  }, [auth, navigate, redirectParam])

  if (auth === null || auth?.loading) {
    return (
      <>
        <AppBar />
        <div className="flex min-h-[50vh] items-center justify-center bg-paper bg-amber-50/70">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
        </div>
      </>
    )
  }
  return null
}
