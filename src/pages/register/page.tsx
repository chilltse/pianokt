import Toast from '@/components/Toast'
import { AppBar } from '@/components'
import { isAuthEnabled, useAuth } from '@/features/auth'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import * as RadixToast from '@radix-ui/react-toast'

const VERIFY_EMAIL_TOAST =
  'A verification email has been sent. Please verify your email to log in.'

export default function RegisterPage() {
  const { signUpWithEmail, signInWithGoogle, error, clearError, loading } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const redirectTo = searchParams.get('redirect') ?? '/'
  const [toastMsg, setToastMsg] = useState<string | null>(null)
  const [toastKey, setToastKey] = useState('')
  const toastKeyRef = useRef(toastKey)

  useEffect(() => {
    clearError()
  }, [clearError])

  function showToast(msg: string) {
    const newKey = Date.now().toString()
    setToastMsg(msg)
    setToastKey(newKey)
    toastKeyRef.current = newKey
  }

  function hideToast(open: boolean) {
    if (open) return
    setToastMsg((msg) => (toastKeyRef.current === toastKey ? '' : msg ?? ''))
  }

  if (!isAuthEnabled()) {
    return (
      <>
        <AppBar />
        <div className="bg-paper bg-amber-50/70 min-h-screen">
          <div className="mx-auto max-w-md px-4 py-12 text-center text-gray-700">
          <p>Registration is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in .env</p>
          <Link to="/" className="mt-4 inline-block text-stone-600 hover:text-stone-800 hover:underline">
            Back to home
          </Link>
          </div>
        </div>
      </>
    )
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    const email = (form.elements.namedItem('email') as HTMLInputElement).value.trim()
    const password = (form.elements.namedItem('password') as HTMLInputElement).value
    if (!email || !password) return
    try {
      await signUpWithEmail(email, password)
      showToast(VERIFY_EMAIL_TOAST)
      setTimeout(() => navigate(redirectTo, { replace: true }), 2000)
    } catch {
      // error set in context
    }
  }

  async function handleGoogle() {
    try {
      await signInWithGoogle()
    } catch {
      // error set in context
    }
  }

  return (
    <>
      <AppBar />
      <div className="bg-paper bg-amber-50/70 min-h-screen">
        <div className="mx-auto max-w-md px-4 py-12">
        <h1 className="text-2xl font-semibold text-gray-900">Create an account</h1>
        <p className="mt-1 text-sm text-gray-600">
          Already have an account?{' '}
          <Link to={`/login${redirectTo !== '/' ? `?redirect=${encodeURIComponent(redirectTo)}` : ''}`} className="text-stone-600 hover:text-stone-800 hover:underline">
            Log in
          </Link>
        </p>
        <form onSubmit={handleSubmit} className="mt-6 space-y-4">
          {error && (
            <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
          )}
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-700">
              Email
            </label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              required
              className="mt-1 block w-full rounded-md border border-stone-300 px-3 py-2 shadow-sm focus:border-stone-500 focus:ring-stone-500"
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="new-password"
              required
              minLength={6}
              className="mt-1 block w-full rounded-md border border-stone-300 px-3 py-2 shadow-sm focus:border-stone-500 focus:ring-stone-500"
            />
            <p className="mt-1 text-xs text-gray-500">At least 6 characters</p>
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-stone-500 px-4 py-2 text-sm font-medium text-stone-50 hover:bg-stone-600 disabled:opacity-50"
          >
            Sign up
          </button>
        </form>
        <div className="mt-6">
          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-gray-300" />
            </div>
            <div className="relative flex justify-center text-sm">
              <span className="bg-amber-50/70 px-2 text-gray-500">Or continue with</span>
            </div>
          </div>
          <button
            type="button"
            onClick={handleGoogle}
            disabled={loading}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
          >
            <svg className="h-5 w-5" viewBox="0 0 24 24">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
            </svg>
            Google
          </button>
        </div>
        <p className="mt-6 text-center">
          <Link to="/" className="text-sm text-stone-600 hover:text-stone-800 hover:underline">
            Back to home
          </Link>
        </p>
        </div>
      </div>
      <Toast
        open={!!toastMsg}
        onOpenChange={hideToast}
        title={toastMsg ?? ''}
        toastKey={toastKey}
      />
      <RadixToast.Viewport className="fixed right-4 bottom-4 z-50 flex w-80 max-w-[100vw] flex-col-reverse gap-3 p-4" />
    </>
  )
}
