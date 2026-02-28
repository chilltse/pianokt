import { useOptionalAuth } from '@/features/auth'
import { useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router'

export default function RecordingsRedirect() {
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
    navigate(`/recordings/${auth.user.id}`, { replace: true })
  }, [auth, navigate, redirectParam])

  return null
}
