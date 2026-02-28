/**
 * 受保护路由的鉴权：仅用 getUser() 做最终放行判断，不使用 getSession()。
 * 未登录 → 重定向 /login?redirect=当前路径
 * 已登录但 URL 的 userId 与当前用户不一致 → 重定向到自己的路径
 */

import type { User } from '@supabase/supabase-js'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { supabase } from '@/features/auth/supabase'
import type { AuthUser } from '@/features/auth/types'

function mapUser(u: User): AuthUser {
  const meta = u.user_metadata ?? {}
  return {
    id: u.id,
    email: u.email ?? meta.email ?? null,
    displayName:
      meta.full_name ?? meta.name ?? meta.given_name ?? (u.email ? u.email.split('@')[0] : null),
    avatarUrl: meta.avatar_url ?? meta.picture ?? null,
  }
}

const PATH_BASE: Record<PathKind, string> = {
  account: '/account',
  recordings: '/recordings',
  'challenge-songs': '/challenge-songs',
}

export type PathKind = 'account' | 'recordings' | 'challenge-songs'

/**
 * 在受保护页面使用：用 getUser() 校验当前用户，不通过则重定向。
 * 返回 { user, loading }，通过校验时 user 为当前用户（AuthUser），loading 为 false。
 */
export function useRequireAuth(pathKind: PathKind): {
  user: AuthUser | null
  loading: boolean
} {
  const params = useParams<{ userId: string }>()
  const navigate = useNavigate()
  const [state, setState] = useState<{ user: AuthUser | null; loading: boolean }>({
    user: null,
    loading: true,
  })

  useEffect(() => {
    if (!supabase) {
      setState({ user: null, loading: false })
      return
    }
    let cancelled = false
    supabase.auth
      .getUser()
      .then(({ data: { user }, error }) => {
        if (cancelled) return
        if (error || !user) {
          const currentPath = window.location.pathname
          navigate(`/login?redirect=${encodeURIComponent(currentPath)}`, { replace: true })
          return
        }
        const routeUserId = params.userId
        const base = PATH_BASE[pathKind]
        if (routeUserId && routeUserId !== user.id) {
          navigate(`${base}/${user.id}`, { replace: true })
          return
        }
        setState({ user: mapUser(user), loading: false })
      })
      .catch(() => {
        if (!cancelled) setState({ user: null, loading: false })
      })
    return () => {
      cancelled = true
    }
  }, [pathKind, params.userId, navigate])

  return state
}
