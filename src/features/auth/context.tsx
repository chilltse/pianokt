import { supabase } from '@/features/auth/supabase'
import type { AuthUser } from '@/features/auth/types'
import type { User, Session } from '@supabase/supabase-js'
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

const AUTH_LOAD_TIMEOUT_MS = 5000

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
  const navigateRef = useRef(navigate)
  navigateRef.current = navigate
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
    const setDone = () => setLoading(false)
    const timeoutId = setTimeout(setDone, AUTH_LOAD_TIMEOUT_MS)

    const checkWhitelistAndSignOutIfNeeded = async () => {
      const { data, error: rpcErr } = await supabase.rpc('check_user_allowed')
      if (rpcErr) return
      if (data === false) {
        await supabase.auth.signOut()
        setUser(null)
        setError('Your account is not on the access list. Contact the administrator.')
        navigateRef.current('/', { replace: true })
        return
      }
      await supabase.rpc('upsert_profile_from_auth').then(() => {}, () => {})
    }

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange(async (_event, session) => {
      updateFromSession(session)
      clearTimeout(timeoutId)
      setLoading(false)
      if (session?.user) {
        checkWhitelistAndSignOutIfNeeded().catch(() => {})
      }
    })

    supabase.auth.getSession().then(async ({ data: { session } }) => {
      updateFromSession(session)
      clearTimeout(timeoutId)
      setLoading(false)
      if (session?.user) {
        checkWhitelistAndSignOutIfNeeded().catch(() => {})
      }
    })

    return () => {
      clearTimeout(timeoutId)
      subscription.unsubscribe()
    }
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
      const isEmailNotConfirmed =
        /email not confirmed|user not confirmed|not confirmed/i.test(e.message)
      setError(
        isEmailNotConfirmed
          ? 'Please check your registration email and click the confirmation link to verify your account before logging in.'
          : e.message
      )
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
