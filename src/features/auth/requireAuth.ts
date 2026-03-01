/**
 * 受保护路由的鉴权：复用 AuthProvider 的 user，避免重复 getUser() 调用。
 * 未登录 → 重定向 /login?redirect=当前路径
 * 已登录但 URL 的 userId 与当前用户不一致 → 重定向到自己的路径
 */

import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router'
import type { AuthUser } from '@/features/auth/types'
import { useOptionalAuth } from '@/features/auth/context'

const PATH_BASE: Record<PathKind, string> = {
  account: '/account',
  recordings: '/recordings',
  'challenge-songs': '/challenge-songs',
}

export type PathKind = 'account' | 'recordings' | 'challenge-songs'

/**
 * 在受保护页面使用：复用 AuthProvider 的 user，不通过则重定向。
 * 返回 { user, loading }，通过校验时 user 为当前用户（AuthUser），loading 为 false。
 */
export function useRequireAuth(pathKind: PathKind): {
  user: AuthUser | null
  loading: boolean
} {
  const params = useParams<{ userId: string }>()
  const navigate = useNavigate()
  const auth = useOptionalAuth()

  useEffect(() => {
    if (auth === null || auth.loading) return
    if (!auth.user) {
      const currentPath = window.location.pathname + (window.location.search || '')
      navigate(`/login?redirect=${encodeURIComponent(currentPath)}`, { replace: true })
      return
    }
    const routeUserId = params.userId
    const base = PATH_BASE[pathKind]
    if (routeUserId && routeUserId !== auth.user.id) {
      navigate(`${base}/${auth.user.id}`, { replace: true })
    }
  }, [auth, pathKind, params.userId, navigate])

  if (auth === null || auth.loading) {
    return { user: null, loading: true }
  }
  if (!auth.user) {
    return { user: null, loading: true }
  }
  const routeUserId = params.userId
  if (routeUserId && routeUserId !== auth.user.id) {
    return { user: null, loading: true }
  }
  return { user: auth.user, loading: false }
}
