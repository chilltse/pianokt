import { AppBar, MarketingFooter, Sizer } from '@/components'
import { AvatarUploadZone } from '@/components/AvatarUploadZone'
import { useAuth, useRequireAuth, updateProfile } from '@/features/auth'
import {
  listChallengeRecordings,
  getBestAccuracyPerSong,
  isChallengeSuccess,
} from '@/features/challenge-history'
import clsx from 'clsx'
import { ChevronDown } from '@/icons'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { supabase } from '@/features/auth/supabase'

type Stats = {
  totalChallenges: number
  avgAccuracy: number
  challengesPassed: number
}

export default function AccountPage() {
  const { user, loading: authLoading } = useRequireAuth('account')
  const { signOut, refreshSession } = useAuth()
  const [stats, setStats] = useState<Stats | null>(null)
  const [loadingStats, setLoadingStats] = useState(true)
  const [displayName, setDisplayName] = useState('')
  const [avatarUrl, setAvatarUrl] = useState('')
  const [profileSaving, setProfileSaving] = useState(false)
  const [profileMessage, setProfileMessage] = useState<'saved' | 'error' | null>(null)
  const [passwordNew, setPasswordNew] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [passwordSaving, setPasswordSaving] = useState(false)
  const [passwordMessage, setPasswordMessage] = useState<'saved' | 'error' | null>(null)
  const [expandEditProfile, setExpandEditProfile] = useState(false)
  const [expandChangePassword, setExpandChangePassword] = useState(false)

  useEffect(() => {
    if (!user) return
    setDisplayName(user.displayName ?? '')
    setAvatarUrl(user.avatarUrl ?? '')
  }, [user])

  useEffect(() => {
    if (!user?.id) return
    setLoadingStats(true)
    listChallengeRecordings()
      .then((res) => {
        if ('error' in res) return
        const rows = res.data
        const totalChallenges = rows.length
        const bestMap = getBestAccuracyPerSong(rows)
        let sumAcc = 0
        let countAcc = 0
        let passed = 0
        for (const [, v] of bestMap) {
          sumAcc += v.bestAccuracy
          countAcc += 1
          if (isChallengeSuccess(v.bestAccuracy)) passed += 1
        }
        setStats({
          totalChallenges,
          avgAccuracy: countAcc > 0 ? sumAcc / countAcc : 0,
          challengesPassed: passed,
        })
      })
      .finally(() => setLoadingStats(false))
  }, [user])

  async function handleSaveProfile(e: React.FormEvent) {
    e.preventDefault()
    setProfileMessage(null)
    setProfileSaving(true)
    const res = await updateProfile({ display_name: displayName || null, avatar_url: avatarUrl || null })
    setProfileSaving(false)
    setProfileMessage(res.error ? 'error' : 'saved')
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault()
    setPasswordMessage(null)
    if (passwordNew.length < 6) {
      setPasswordMessage('error')
      return
    }
    if (passwordNew !== passwordConfirm) {
      setPasswordMessage('error')
      return
    }
    setPasswordSaving(true)
    if (!supabase) {
      setPasswordSaving(false)
      setPasswordMessage('error')
      return
    }
    const { error } = await supabase.auth.updateUser({ password: passwordNew })
    setPasswordSaving(false)
    setPasswordMessage(error ? 'error' : 'saved')
    if (!error) {
      setPasswordNew('')
      setPasswordConfirm('')
    }
  }

  if (authLoading || !user) {
    return (
      <>
        <AppBar />
        <div className="flex min-h-screen items-center justify-center bg-paper bg-amber-50/70">
          <p className="text-gray-500">Loading…</p>
        </div>
      </>
    )
  }

  return (
    <>
      <title>Account</title>
      <div className="flex min-h-screen flex-col bg-paper bg-amber-50/70">
        <AppBar />
        <div className="mx-auto w-full max-w-(--breakpoint-lg) flex-1 px-6 py-10">
          <h1 className="text-2xl font-semibold text-gray-900">Account</h1>
          <Sizer height={24} />

          {/* Stats */}
          <section className="rounded-xl border border-amber-100 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-medium text-gray-900">Your stats</h2>
            <Sizer height={16} />
            {loadingStats ? (
              <p className="text-sm text-gray-500">Loading…</p>
            ) : stats ? (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                <div className="rounded-lg bg-amber-50/80 p-4">
                  <div className="text-2xl font-semibold text-gray-900">{stats.totalChallenges}</div>
                  <div className="text-xs text-gray-600">Challenges completed</div>
                </div>
                <div className="rounded-lg bg-amber-50/80 p-4">
                  <div className="text-2xl font-semibold text-gray-900">
                    {stats.avgAccuracy.toFixed(1)}%
                  </div>
                  <div className="text-xs text-gray-600">Avg. accuracy (best per song)</div>
                </div>
                <div className="rounded-lg bg-amber-50/80 p-4">
                  <div className="text-2xl font-semibold text-gray-900">{stats.challengesPassed}</div>
                  <div className="text-xs text-gray-600">Passed (≥90%)</div>
                </div>
              </div>
            ) : (
              <p className="text-sm text-gray-500">No challenge data yet.</p>
            )}
          </section>
          <Sizer height={24} />

          {/* Edit profile — collapsible */}
          <CollapsibleSection
            title="Edit profile"
            expanded={expandEditProfile}
            onToggle={() => setExpandEditProfile((v) => !v)}
          >
            <form onSubmit={handleSaveProfile} className="space-y-4">
              <div>
                <label htmlFor="account-display-name" className="block text-sm font-medium text-gray-700">
                  Display name
                </label>
                <input
                  id="account-display-name"
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  className="mt-1 block w-full max-w-md rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-amber-500 focus:ring-amber-500"
                  placeholder="Your name"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">Avatar</label>
                <Sizer height={8} />
                <AvatarUploadZone
                  currentUrl={avatarUrl || null}
                  onUploadSuccess={(url) => {
                    setAvatarUrl(`${url}?t=${Date.now()}`)
                    updateProfile({ avatar_url: url }).then(async (res) => {
                      setProfileMessage(res.error ? 'error' : 'saved')
                      if (!res.error) await refreshSession()
                    })
                  }}
                />
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="submit"
                  disabled={profileSaving}
                  className="rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
                >
                  {profileSaving ? 'Saving…' : 'Save profile'}
                </button>
                {profileMessage === 'saved' && (
                  <span className="text-sm text-green-600">Saved.</span>
                )}
                {profileMessage === 'error' && (
                  <span className="text-sm text-red-600">Failed to save.</span>
                )}
              </div>
            </form>
          </CollapsibleSection>
          <Sizer height={24} />

          {/* Change password — collapsible */}
          <CollapsibleSection
            title="Change password"
            expanded={expandChangePassword}
            onToggle={() => setExpandChangePassword((v) => !v)}
          >
            <form onSubmit={handleChangePassword} className="max-w-md space-y-4">
              <div>
                <label htmlFor="account-password-new" className="block text-sm font-medium text-gray-700">
                  New password
                </label>
                <input
                  id="account-password-new"
                  type="password"
                  value={passwordNew}
                  onChange={(e) => setPasswordNew(e.target.value)}
                  minLength={6}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-amber-500 focus:ring-amber-500"
                  placeholder="At least 6 characters"
                />
              </div>
              <div>
                <label htmlFor="account-password-confirm" className="block text-sm font-medium text-gray-700">
                  Confirm new password
                </label>
                <input
                  id="account-password-confirm"
                  type="password"
                  value={passwordConfirm}
                  onChange={(e) => setPasswordConfirm(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 shadow-sm focus:border-amber-500 focus:ring-amber-500"
                  placeholder="Repeat new password"
                />
              </div>
              <div className="flex items-center gap-3">
                <button
                  type="submit"
                  disabled={passwordSaving || !passwordNew || passwordNew !== passwordConfirm}
                  className="rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
                >
                  {passwordSaving ? 'Updating…' : 'Update password'}
                </button>
                {passwordMessage === 'saved' && (
                  <span className="text-sm text-green-600">Password updated.</span>
                )}
                {passwordMessage === 'error' && (
                  <span className="text-sm text-red-600">Failed to update password.</span>
                )}
              </div>
            </form>
          </CollapsibleSection>
          <Sizer height={24} />

          {/* Links */}
          <section className="rounded-xl border border-amber-100 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-medium text-gray-900">Quick links</h2>
            <Sizer height={12} />
            <div className="flex flex-wrap gap-4">
              {user && (
                <Link
                  to={`/recordings/${user.id}`}
                  className="text-sm font-medium text-amber-700 hover:text-amber-800 hover:underline"
                >
                  My recordings
                </Link>
              )}
              <button
                type="button"
                onClick={() => signOut()}
                className="text-sm font-medium text-gray-600 hover:text-gray-800 hover:underline"
              >
                Log out
              </button>
            </div>
          </section>
        </div>
        <MarketingFooter />
      </div>
    </>
  )
}

function CollapsibleSection({
  title,
  expanded,
  onToggle,
  children,
}: {
  title: string
  expanded: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <section className="rounded-xl border border-amber-100 bg-white shadow-sm overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between px-6 py-4 text-left hover:bg-gray-50/80 transition-colors"
        aria-expanded={expanded}
      >
        <h2 className="text-lg font-medium text-gray-900">{title}</h2>
        <ChevronDown
          className={clsx('h-5 w-5 shrink-0 text-gray-500 transition-transform duration-200', expanded && 'rotate-180')}
          aria-hidden
        />
      </button>
      <div
        className="grid transition-[grid-template-rows] duration-300 ease-out"
        style={{ gridTemplateRows: expanded ? '1fr' : '0fr' }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="border-t border-amber-100 px-6 pb-6 pt-4">
            {children}
          </div>
        </div>
      </div>
    </section>
  )
}
