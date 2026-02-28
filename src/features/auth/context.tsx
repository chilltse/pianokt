import { supabase } from '@/features/auth/supabase'
import type { AuthUser } from '@/features/auth/types'
import type { User, Session } from '@supabase/supabase-js'
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

function mapUser(u: User | null): AuthUser | null {
  if (!u) return null
  const meta = u.user_metadata ?? {}
  return {
    id: u.id,
    email: u.email ?? meta.email ?? null,
    displayName:
      meta.full_name ?? meta.name ?? meta.given_name ?? (u.email ? u.email.split('@')[0] : null),
    avatarUrl: meta.avatar_url ?? meta.picture ?? null,
  }
}

type AuthContextValue = {
  user: AuthUser | null
  loading: boolean
  error: string | null
  signUpWithEmail: (email: string, password: string) => Promise<void>
  signInWithEmail: (email: string, password: string) => Promise<void>
  signInWithGoogle: () => Promise<void>
  signOut: () => Promise<void>
  clearError: () => void
  /** 强制用当前 session 刷新 user（例如更新头像/昵称后让导航栏立即更新） */
  refreshSession: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate()
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const updateFromSession = useCallback((session: Session | null) => {
    setUser(mapUser(session?.user ?? null))
  }, [])

  useEffect(() => {
    if (!supabase) {
      setLoading(false)
      return () => {}
    }
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      updateFromSession(session)
      setLoading(false)
    })
    supabase.auth.getSession().then(({ data: { session } }) => {
      updateFromSession(session)
      setLoading(false)
    })
    return () => subscription.unsubscribe()
  }, [updateFromSession])

  const signUpWithEmail = useCallback(async (email: string, password: string) => {
    setError(null)
    if (!supabase) throw new Error('Auth not configured')
    const { error: e } = await supabase.auth.signUp({ email, password })
    if (e) {
      setError(e.message)
      throw e
    }
  }, [])

  const signInWithEmail = useCallback(async (email: string, password: string) => {
    setError(null)
    if (!supabase) throw new Error('Auth not configured')
    const { error: e } = await supabase.auth.signInWithPassword({ email, password })
    if (e) {
      setError(e.message)
      throw e
    }
  }, [])

  const signInWithGoogle = useCallback(async () => {
    setError(null)
    if (!supabase) throw new Error('Auth not configured')
    const { error: e } = await supabase.auth.signInWithOAuth({ provider: 'google' })
    if (e) {
      setError(e.message)
      throw e
    }
  }, [])

  const signOut = useCallback(async () => {
    setError(null)
    if (supabase) await supabase.auth.signOut()
    setUser(null)
    navigate('/', { replace: true })
  }, [navigate])

  const clearError = useCallback(() => setError(null), [])

  const refreshSession = useCallback(async () => {
    if (!supabase) return
    const { data: { session } } = await supabase.auth.getSession()
    updateFromSession(session)
  }, [updateFromSession])

  const value = useMemo(
    () => ({
      user,
      loading,
      error,
      signUpWithEmail,
      signInWithEmail,
      signInWithGoogle,
      signOut,
      clearError,
      refreshSession,
    }),
    [
      user,
      loading,
      error,
      signUpWithEmail,
      signInWithEmail,
      signInWithGoogle,
      signOut,
      clearError,
      refreshSession,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext)
}
