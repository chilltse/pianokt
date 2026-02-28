import { useOptionalAuth } from '@/features/auth'
import { useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router'
import { resolveAuthRedirect } from '@/utils/authRedirect'

export default function AccountRedirect() {
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
    navigate(`/account/${auth.user.id}`, { replace: true })
  }, [auth, navigate, redirectParam])

  return null
}
