/** Visit lifecycle and a durable, user-scoped event outbox. No route owns a visit. */
export type Visit = {
  id: string
  started_at: string
  occurred_at: string
  sequence: number
  state: 'active' | 'pagehide' | 'signed_out'
}
export type AttemptContext = {
  attempt_id: string
  visit_attempt_sequence: number
  song_id: string
  song_source: string
  started_at: string
}
export type ActivityEvent = {
  event_id: string
  event_sequence: number
  event_type: 'play_started' | 'paused' | 'resumed' | 'finished' | 'exited' | 'interrupted'
  occurred_at: string
  song_time_sec: number
  elapsed_playing_sec: number
  metadata: Record<string, unknown>
}
export type Activity = {
  owner_id: string
  visit: Visit
  attempt?: AttemptContext
  events: ActivityEvent[]
}
type Saved = {
  visit: Visit
  attemptCount: number
  active?: { context: AttemptContext; last: ActivityEvent }
}
type Dependencies = {
  session: Storage
  durable: Storage
  now: () => string
  uuid: () => string
  send: (body: Activity) => Promise<void>
}

export class ActivityTracker {
  private saved: Saved
  private flushing = false
  private readonly sessionKey: string
  private readonly prefix: string
  constructor(
    readonly userId: string,
    private deps: Dependencies,
    restore: boolean,
    openedAt: string,
  ) {
    this.sessionKey = `pianokt-visit:${userId}`
    this.prefix = `pianokt-activity:${userId}:`
    let previous: Saved | null = null
    try {
      previous = JSON.parse(deps.session.getItem(this.sessionKey) ?? 'null')
    } catch {
      /* fresh visit */
    }
    this.saved =
      restore && previous?.visit.state !== 'signed_out' && previous
        ? previous
        : {
            visit: {
              id: deps.uuid(),
              started_at: openedAt,
              occurred_at: openedAt,
              sequence: 0,
              state: 'active',
            },
            attemptCount: 0,
          }
    // Refresh retains the visit but cannot resume an in-memory MIDI recording.
    if (this.saved.active) this.interrupt('reload')
    this.touch('active')
  }
  private persist() {
    this.deps.session.setItem(this.sessionKey, JSON.stringify(this.saved))
  }
  private snapshot(state: Visit['state'] = 'active'): Visit {
    const visit = this.saved.visit
    visit.sequence += 1
    visit.occurred_at = this.deps.now()
    visit.state = state
    this.persist()
    return { ...visit }
  }
  private enqueue(body: Activity, suffix: string) {
    // Persist before requesting the server, so a close/network failure can be retried.
    this.deps.durable.setItem(this.prefix + suffix, JSON.stringify(body))
    void this.flush()
  }
  touch(state: Visit['state']) {
    this.enqueue(
      { owner_id: this.userId, visit: this.snapshot(state), events: [] },
      `visit:${this.saved.visit.id}`,
    )
  }
  begin(attemptId: string, songId: string, songSource: string, startedAt: string): AttemptContext {
    if (this.saved.active) this.interrupt('new_attempt')
    this.saved.attemptCount += 1
    const context = {
      attempt_id: attemptId,
      visit_attempt_sequence: this.saved.attemptCount,
      song_id: songId,
      song_source: songSource,
      started_at: startedAt,
    }
    this.persist()
    return context
  }
  record(context: AttemptContext, event: ActivityEvent) {
    if (['finished', 'exited', 'interrupted'].includes(event.event_type))
      this.saved.active = undefined
    else this.saved.active = { context, last: event }
    const visit = this.saved.visit.state === 'active' ? this.snapshot() : { ...this.saved.visit }
    this.persist()
    this.enqueue(
      { owner_id: this.userId, visit, attempt: context, events: [event] },
      event.event_id,
    )
  }
  interrupt(reason: string, songTime?: number, elapsed?: number) {
    const active = this.saved.active
    if (!active) return
    this.record(active.context, {
      ...active.last,
      event_id: this.deps.uuid(),
      event_sequence: active.last.event_sequence + 1,
      event_type: 'interrupted',
      song_time_sec: songTime ?? active.last.song_time_sec,
      elapsed_playing_sec: elapsed ?? active.last.elapsed_playing_sec,
      occurred_at: this.deps.now(),
      metadata: { reason, position_is_last_observed: songTime === undefined },
    })
  }
  packet(context: AttemptContext, events: ActivityEvent[]): Activity {
    return { owner_id: this.userId, visit: { ...this.saved.visit }, attempt: context, events }
  }
  async flush() {
    if (this.flushing) return
    this.flushing = true
    try {
      const keys = Array.from({ length: this.deps.durable.length }, (_, i) =>
        this.deps.durable.key(i),
      ).filter((k): k is string => !!k?.startsWith(this.prefix))
      for (const key of keys) {
        const data = this.deps.durable.getItem(key)
        if (!data) continue
        try {
          await this.deps.send(JSON.parse(data))
          // A heartbeat may have replaced the item while the request was in flight.
          if (this.deps.durable.getItem(key) === data) this.deps.durable.removeItem(key)
        } catch (error) {
          const status = (error as { status?: number })?.status
          if (status && [400, 403, 409, 422].includes(status)) {
            // Keep rejected evidence for diagnosis without blocking every subsequent event.
            this.deps.durable.setItem(
              key.replace('pianokt-activity:', 'pianokt-activity-rejected:'),
              data,
            )
            if (this.deps.durable.getItem(key) === data) this.deps.durable.removeItem(key)
            console.error('[Activity] Server rejected queued evidence', status)
            continue
          }
          break // Network/auth failures retry after reconnect or token refresh.
        }
      }
    } finally {
      this.flushing = false
    }
  }
}
