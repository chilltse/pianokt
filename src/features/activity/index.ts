import { ActivityTracker, type Activity, type ActivityEvent, type AttemptContext } from './tracker'

export type { Activity, ActivityEvent, AttemptContext } from './tracker'
const apiRoot = (import.meta.env.VITE_PIANOKT_API_URL as string | undefined)?.replace(/\/$/, '')
const openedAt = new Date().toISOString()
let tracker: ActivityTracker | null = null
let token: string | null = null
let dispose: (() => void) | null = null
let initialized = false
const attemptOwners = new WeakMap<AttemptContext, ActivityTracker>()

async function sendActivity(body: Activity, accessToken = token) {
  if (!apiRoot || !accessToken) throw new Error('Activity API unavailable')
  const response = await fetch(`${apiRoot}/activity`, {
    method: 'POST',
    keepalive: true,
    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok)
    throw Object.assign(new Error(`Activity ${response.status}`), { status: response.status })
}

/** Called on authentication changes, NOT on route changes. */
export function setActivitySession(
  session: { user: { id: string }; access_token: string } | null,
  forceNewVisit = false,
) {
  if (typeof window === 'undefined') return
  if (!forceNewVisit && tracker?.userId === session?.user.id) {
    token = session?.access_token ?? null
    return
  }
  if (tracker && !forceNewVisit) {
    tracker.interrupt('signed_out')
    tracker.touch('signed_out')
  }
  dispose?.()
  dispose = null
  tracker = null
  token = session?.access_token ?? null
  if (!session || !apiRoot) return
  const ownerId = session.user.id
  let ownerToken = session.access_token
  const navigation = performance.getEntriesByType('navigation')[0] as
    | PerformanceNavigationTiming
    | undefined
  tracker = new ActivityTracker(
    ownerId,
    {
      session: sessionStorage,
      durable: localStorage,
      now: () => new Date().toISOString(),
      uuid: () => crypto.randomUUID(),
      send: (body) => {
        // Never submit a previous user's persisted queue with a new user's token.
        if (tracker?.userId === ownerId && token) ownerToken = token
        return sendActivity(body, ownerToken)
      },
    },
    !initialized && navigation?.type === 'reload',
    initialized ? new Date().toISOString() : openedAt,
  )
  initialized = true
  const current = tracker
  const online = () => {
    current.touch('active')
    void current.flush()
  }
  const hide = () => {
    current.touch('pagehide')
  }
  const visible = () => {
    if (document.visibilityState === 'visible') online()
  }
  const show = (event: PageTransitionEvent) => {
    if (event.persisted) {
      // Back from another document is a new visit, even if the browser cached this page.
      setActivitySession({ user: { id: ownerId }, access_token: token ?? ownerToken }, true)
    } else online()
  }
  // Hidden tabs remain visits; pagehide is provisional because refresh can resume it.
  window.addEventListener('pagehide', hide)
  window.addEventListener('pageshow', show)
  window.addEventListener('online', online)
  document.addEventListener('visibilitychange', visible)
  const interval = window.setInterval(online, 30_000)
  dispose = () => {
    clearInterval(interval)
    window.removeEventListener('pagehide', hide)
    window.removeEventListener('pageshow', show)
    window.removeEventListener('online', online)
    document.removeEventListener('visibilitychange', visible)
  }
}

export function beginAttempt(id: string, songId: string, source: string, startedAt: string) {
  if (!tracker) return null
  const context = tracker.begin(id, songId, source, startedAt)
  attemptOwners.set(context, tracker)
  return context
}
export function recordAttemptEvent(context: AttemptContext | null, event: ActivityEvent) {
  if (context) attemptOwners.get(context)?.record(context, event)
}
export function interruptAttempt(reason: string, songTime?: number, elapsed?: number) {
  tracker?.interrupt(reason, songTime, elapsed)
}
export function activityPacket(
  context: AttemptContext | null,
  events: ActivityEvent[],
): Activity | undefined {
  return context ? attemptOwners.get(context)?.packet(context, events) : undefined
}
