import {
  beginAttempt,
  recordAttemptEvent,
  type ActivityEvent,
  type AttemptContext,
} from '@/features/activity'
import type { PlayEventType } from '@/features/challenge-history'
import type { OfficialMidiIdentity } from '@/types'
import { useRef, useState } from 'react'

/** Attempt wall-clock accounting and immutable event sequence, independent of MIDI/player UI. */
export function useChallengeAttempt(options: {
  officialIdentity?: OfficialMidiIdentity
  songId: string
  source: string
  songTime: () => number
  songDuration: () => number
  difficulty: number | null
  isMounted: () => boolean
  resetAssignment: () => void
}) {
  const playSessionIdRef = useRef<string | null>(null)
  const attemptContextRef = useRef<AttemptContext | null>(null)
  const pendingTransportEventRef = useRef<Extract<
    PlayEventType,
    'play_started' | 'resumed'
  > | null>(null)
  // Assignment is armed before a count-in, whereas a valid challenge cycle
  // only starts when the score transport actually starts. Keep those states
  // distinct so a rapid count-in cancel/pause cannot manufacture a cycle that
  // begins with `paused` or `resumed`.
  const recordedTransportStartedRef = useRef(false)
  const challengeEventsRef = useRef<
    Array<{
      eventId: string
      eventType: Extract<
        PlayEventType,
        'play_started' | 'paused' | 'resumed' | 'finished' | 'exited'
      >
      songTimeSec: number
      elapsedPlayingSec: number
      clientTs: string
      metadata: Record<string, unknown>
    }>
  >([])
  const playStartedAtMsRef = useRef<number | null>(null)
  const accumulatedPlayMsRef = useRef(0)
  const terminalFlowInFlightRef = useRef(false)
  // Mirrors terminalFlowInFlightRef for rendering; the ref is what guards synchronously.
  const [isFinalizing, setIsFinalizing] = useState(false)
  const markPlayingStarted = () => {
    if (playStartedAtMsRef.current == null) {
      playStartedAtMsRef.current = performance.now()
    }
  }

  const markPlayingStopped = () => {
    if (playStartedAtMsRef.current == null) return
    accumulatedPlayMsRef.current += performance.now() - playStartedAtMsRef.current
    playStartedAtMsRef.current = null
  }

  const getTimePlayingSec = () => {
    let elapsedMs = accumulatedPlayMsRef.current
    if (playStartedAtMsRef.current != null) {
      elapsedMs += performance.now() - playStartedAtMsRef.current
    }
    return Math.max(0, elapsedMs / 1000)
  }

  const setTerminalFlowInFlight = (value: boolean) => {
    terminalFlowInFlightRef.current = value
    if (options.isMounted()) setIsFinalizing(value)
  }

  const resetPlaySessionTracking = () => {
    playSessionIdRef.current = null
    challengeEventsRef.current = []
    attemptContextRef.current = null
    options.resetAssignment()
    pendingTransportEventRef.current = null
    recordedTransportStartedRef.current = false
    playStartedAtMsRef.current = null
    accumulatedPlayMsRef.current = 0
    setTerminalFlowInFlight(false)
  }

  const recordChallengeEvent = (
    eventType: Extract<
      PlayEventType,
      'play_started' | 'paused' | 'resumed' | 'finished' | 'exited'
    >,
    metadata: Record<string, unknown> = {},
    songTimeSecOverride?: number,
    sessionIdOverride?: string,
  ): void => {
    const sessionId = sessionIdOverride ?? playSessionIdRef.current
    if (!sessionId) return
    const clientTs = new Date().toISOString()
    if (eventType === 'play_started') {
      attemptContextRef.current = beginAttempt(
        sessionId,
        options.songId,
        options.source,
        clientTs,
        options.officialIdentity,
      )
    }
    challengeEventsRef.current.push({
      eventId: crypto.randomUUID(),
      eventType,
      songTimeSec: songTimeSecOverride ?? options.songTime(),
      clientTs,
      elapsedPlayingSec: Number(getTimePlayingSec().toFixed(3)),
      metadata: {
        source: options.source,
        song_duration_sec: options.songDuration(),
        difficulty: options.difficulty,
        ...metadata,
      },
    })
    const event = challengeEventsRef.current[challengeEventsRef.current.length - 1]
    recordAttemptEvent(
      attemptContextRef.current,
      toActivityEvent(event, challengeEventsRef.current.length - 1),
    )
  }

  const toActivityEvent = (
    event: (typeof challengeEventsRef.current)[number],
    index: number,
  ): ActivityEvent => ({
    event_id: event.eventId,
    event_sequence: index,
    event_type: event.eventType,
    occurred_at: event.clientTs,
    song_time_sec: event.songTimeSec,
    elapsed_playing_sec: event.elapsedPlayingSec,
    metadata: event.metadata,
  })

  return {
    accumulatedPlayMsRef,
    playSessionIdRef,
    attemptContextRef,
    pendingTransportEventRef,
    recordedTransportStartedRef,
    challengeEventsRef,
    terminalFlowInFlightRef,
    isFinalizing,
    markPlayingStarted,
    markPlayingStopped,
    getTimePlayingSec,
    setTerminalFlowInFlight,
    resetPlaySessionTracking,
    recordChallengeEvent,
    toActivityEvent,
  }
}
