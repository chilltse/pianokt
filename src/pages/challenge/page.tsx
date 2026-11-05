import Toast from '@/components/Toast'
import { activityPacket, interruptAttempt } from '@/features/activity'
import {
  isChallengeSuccess,
  localAlignmentLabEnabled,
  runLocalAlignment,
  saveChallengeRecording,
} from '@/features/challenge-history'
import type { PlayEventType } from '@/features/challenge-history'
import { referenceSnapshot } from '@/features/challenge-history/referenceSnapshot'
import { useSongScrubTimes } from '@/features/controls'
import { useSong } from '@/features/data'
import { useSongMetadata } from '@/features/data/library'
import midiState, {
  getMidiInputs,
  useSegmentedRecordMidi,
  type PerformanceEventsAudit,
} from '@/features/midi'
import { usePlayer } from '@/features/player'
import {
  getDefaultSongSettings,
  getHandSettings,
  getSongSettings,
  SongVisualizer,
} from '@/features/SongVisualization'
import { getSynthStub } from '@/features/synth'
import {
  useEventListener,
  useLazyStableRef,
  useOnUnmount,
  usePlayerState,
  useSongSettings,
  useWakeLock,
} from '@/hooks'
import { TopBar } from '@/pages/play/components'
import CountdownOverlay from '@/pages/play/components/CountdownOverlay'
import { StatsPopup } from '@/pages/play/components/StatsPopup'
import { MidiStateEvent, SongConfig, SongSource } from '@/types'
import { bytesToBase64 } from '@/utils'
import * as RadixToast from '@radix-ui/react-toast'
import clsx from 'clsx'
import { useAtomValue } from 'jotai'
import { AlertCircle, ArrowLeft } from 'lucide-react'
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router'
import ChallengeSuccessModal, { type ChallengeEndVariant } from './ChallengeSuccessModal'
import { LocalAlignmentTimeline } from './LocalAlignmentTimeline'
import { useChallengeAttempt } from './useChallengeAttempt'

function SongNotFound({ songTitle, onGoBack }: { songTitle?: string; onGoBack: () => void }) {
  return (
    <div className="bg-paper flex h-screen items-center justify-center bg-amber-50/70">
      <div className="mx-auto max-w-md rounded-lg border border-amber-100 bg-white p-6 text-center shadow-lg">
        <div className="mb-4">
          <AlertCircle className="mx-auto h-12 w-12 text-red-500" />
        </div>
        <h2 className="mb-2 text-lg font-medium text-gray-900">Song Not Found</h2>
        {songTitle && (
          <p className="mb-4 text-sm text-gray-600">
            Could not load "{songTitle}". The file may have been moved or deleted.
          </p>
        )}
        <button
          onClick={onGoBack}
          className="mx-auto flex cursor-pointer items-center gap-2 rounded-md bg-black px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-gray-800"
        >
          <ArrowLeft className="h-4 w-4" />
          Go Back to Song List
        </button>
      </div>
    </div>
  )
}

export default function ChallengePage() {
  const [searchParams] = useSearchParams()
  const source = searchParams.get('source') as SongSource | null
  const id = searchParams.get('id')
  if (!source || !id) return <Navigate to="/" replace />
  return <ChallengeAttempt key={`${source}:${id}`} source={source} id={id} />
}

function ChallengeAttempt({ source, id }: { source: SongSource; id: string }) {
  const navigate = useNavigate()
  const player = usePlayer()
  const playerState = usePlayerState()
  const countdownTotal = useAtomValue(player.countdownTotal)
  const countdownRemaining = useAtomValue(player.countdownRemaining)
  const synth = useLazyStableRef(() => getSynthStub('acoustic_grand_piano'))
  let { data: song, error, isLoading } = useSong(id, source)
  let songMeta = useSongMetadata(id, source)
  const [songConfig, setSongConfig] = useSongSettings(id)
  const [isStatsVisible, setIsStatsVisible] = useState(false)
  const [showSuccessModal, setShowSuccessModal] = useState(false)
  const [endModalVariant, setEndModalVariant] = useState<ChallengeEndVariant>('success')
  const [isConfirmExitOpen, setIsConfirmExitOpen] = useState(false)
  const [toastMsg, setToastMsg] = useState<string | null>('')
  const [localAlignment, setLocalAlignment] = useState<Record<string, any> | null>(null)
  const [toastKey, setToastKey] = useState<string>('')
  const toastKeyRef = useRef(toastKey)
  // Frozen when a Challenge transport is armed. Countdown consumes wall time
  // but not song time, so this preserves the exact score origin that playback
  // will use once it actually begins.
  const challengeAssignmentRef = useRef<{
    range: { start: number; end: number }
    config: SongConfig
  } | null>(null)
  // The upload outlives this route, so toasts must not fire after unmount.
  const isMountedRef = useRef(true)
  const { currentTime, duration } = useSongScrubTimes()

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  const range = useAtomValue(player.getRange())
  const selectedRange = useMemo(
    () => (range ? { start: range[0], end: range[1] } : undefined),
    [range],
  )

  useWakeLock()
  useOnUnmount(() => {
    markPlayingStopped()
    interruptAttempt('route_left', nowSongSec(), getTimePlayingSec())
    player.stop()
  })

  const hand =
    songConfig.left && songConfig.right
      ? 'both'
      : songConfig.left
        ? 'left'
        : songConfig.right
          ? 'right'
          : 'none'

  const { waiting, left, right } = songConfig
  useEffect(() => {
    player.setWait(waiting)
    if (left && right) {
      player.setHand('both')
    } else {
      player.setHand(left ? 'left' : 'right')
    }
  }, [waiting, left, right, player])

  const metronome = songConfig.metronome ?? getDefaultSongSettings(song ?? undefined).metronome
  const countdownEnabled =
    songConfig.countdownEnabled ?? getDefaultSongSettings(song ?? undefined).countdownEnabled
  const transpose = songConfig.transpose ?? getDefaultSongSettings(song ?? undefined).transpose

  useEffect(() => {
    if (!songConfig.metronome) {
      setSongConfig({ ...songConfig, metronome })
    }
  }, [metronome, setSongConfig, songConfig])

  useEffect(() => {
    player.applyMetronomeConfig(metronome)
  }, [metronome, player])
  useEffect(() => {
    player.applyCountdownConfig(countdownEnabled)
  }, [countdownEnabled, player])
  useEffect(() => {
    player.applyTransposeConfig(transpose)
  }, [transpose, player])

  useEffect(() => {
    if (!song) return
    const config = getSongSettings(id, song)
    setSongConfig(config)
    player.setSong(song, config)
  }, [song, setSongConfig, id, player])

  // =========================
  // ✅ 录音：歌曲时间基准
  // =========================
  const nowSongSec = (monotonicTimestampMs?: number) => {
    // 你项目里 player.getTime() / player.currentSongTime 可能是秒
    // MIDI event handler 传入原始事件的 monotonic timestamp 时，优先让 player
    // reconstruct that instant's song time instead of using the delayed JS callback time.
    const timestamp =
      monotonicTimestampMs ?? (typeof performance === 'undefined' ? undefined : performance.now())
    const at = (player as any).getTimeAt?.(timestamp)
    if (typeof at === 'number' && Number.isFinite(at)) return at

    // 旧 player 实现没有 getTimeAt() 时，保留既有回退路径。
    const t = (player as any).getTime?.()
    if (typeof t === 'number' && Number.isFinite(t)) return t
    const ct = (player as any).currentSongTime
    if (typeof ct === 'number' && Number.isFinite(ct)) return ct
    return 0
  }

  const {
    isRecording: isRecordingMidi,
    startOrResumeRecording,
    pauseRecording,
    stopRecording,
    flushSilenceTo,
  } = useSegmentedRecordMidi(midiState, nowSongSec)

  const {
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
  } = useChallengeAttempt({
    songId: id,
    source,
    songTime: nowSongSec,
    songDuration: () => getSongDurationSec(),
    difficulty: songMeta?.difficulty ?? null,
    isMounted: () => isMountedRef.current,
    resetAssignment: () => {
      challengeAssignmentRef.current = null
    },
  })

  function showToast(msg: string) {
    if (!isMountedRef.current) {
      console.info('[Challenge]', msg)
      return
    }
    const newKey = Date.now().toString()
    setToastMsg(msg)
    setToastKey(newKey)
    toastKeyRef.current = newKey
  }

  function hideToast(open: boolean) {
    if (open) return
    setToastMsg((msg) => {
      if (toastKeyRef.current === toastKey) {
        return ''
      }
      return msg
    })
  }

  const getSongDurationSec = () => {
    const dur = (player as any).getDuration?.() ?? 0
    if (typeof dur === 'number' && Number.isFinite(dur) && dur > 0) return dur
    const fallback = lastDurationRef.current
    return Number.isFinite(fallback) && fallback > 0 ? fallback : 0
  }

  useEventListener<PageTransitionEvent>('pagehide', () => {
    if (!recordedTransportStartedRef.current || !playSessionIdRef.current) return
    const songTime = nowSongSec()
    markPlayingStopped()
    interruptAttempt('page_left', songTime, getTimePlayingSec())
    stopRecording(songTime)
    player.stop()
    resetPlaySessionTracking()
  })

  const freezeChallengeAssignment = () => {
    if (challengeAssignmentRef.current) return challengeAssignmentRef.current
    const requestedSongTime = nowSongSec()
    const durationSec = getSongDurationSec() || song?.duration || requestedSongTime
    const configuredEnd = selectedRange?.end ?? durationSec
    // Player restarts a natural end from zero in startPlayback_(). Mirror that
    // planned score position instead of freezing the terminal duration.
    const start =
      durationSec > 0 && requestedSongTime >= durationSec
        ? (selectedRange?.start ?? 0)
        : Math.max(0, Math.min(requestedSongTime, configuredEnd))
    const assignment = {
      range: { start, end: Math.max(start, configuredEnd) },
      config: JSON.parse(JSON.stringify(songConfig)) as SongConfig,
    }
    challengeAssignmentRef.current = assignment
    return assignment
  }

  const beginRecordedTransport = (
    eventType: Extract<PlayEventType, 'play_started' | 'resumed'>,
  ) => {
    if (terminalFlowInFlightRef.current || !playSessionIdRef.current) return
    const songTimeSec = nowSongSec()
    if (eventType === 'play_started') {
      freezeChallengeAssignment()
    }
    markPlayingStarted()
    recordChallengeEvent(eventType, {}, songTimeSec)
    startOrResumeRecording(songTimeSec, challengeAssignmentRef.current?.range.start)
    recordedTransportStartedRef.current = true
  }

  const requestChallengeTransport = (
    eventType: Extract<PlayEventType, 'play_started' | 'resumed'>,
  ) => {
    if (eventType === 'play_started') freezeChallengeAssignment()
    pendingTransportEventRef.current = eventType
    player.play()
    // With no countdown `play()` transitions synchronously. During countdown
    // this remains pending until the effect below observes actual playback.
    if (player.isPlaying()) {
      pendingTransportEventRef.current = null
      beginRecordedTransport(eventType)
    }
  }

  useEffect(() => {
    // `usePlayerState().playing` intentionally includes CountingDown for the
    // ordinary player UI. Challenge evidence must begin only after the score
    // transport itself starts, otherwise count-in key presses become part of
    // the recorded take.
    if (playerState.countingDown || !playerState.playing || !pendingTransportEventRef.current)
      return
    const eventType = pendingTransportEventRef.current
    pendingTransportEventRef.current = null
    beginRecordedTransport(eventType)
  }, [playerState.playing, playerState.countingDown, startOrResumeRecording])

  const saveChallengeRecordingFromBytes = async (params: {
    sessionId: string
    midiBytes: Uint8Array | null
    performanceEvents: PerformanceEventsAudit
    referenceMidiBase64: string
    referenceConfig: SongConfig
    observedRange: { start: number; end: number }
    assignedRange: { start: number; end: number }
    durationSec: number
    accuracy: number
    playedUntilSec: number
    cycle: { sessionId: string; events: typeof challengeEventsRef.current }
  }): Promise<string | null> => {
    const { midiBytes, durationSec, accuracy } = params
    if (!song || !midiBytes || midiBytes.length === 0) {
      console.warn('[Challenge] No MIDI bytes to save recording.')
      return null
    }

    const base64 = bytesToBase64(midiBytes)
    const difficulty = songMeta?.difficulty ?? 0

    if (localAlignmentLabEnabled) {
      try {
        const result = await runLocalAlignment({
          performanceMidiBase64: base64,
          referenceMidiBase64: params.referenceMidiBase64,
          performanceEvents: params.performanceEvents,
          observedEndSec: params.observedRange.end - params.observedRange.start,
          referenceOriginSec: params.observedRange.start,
        })
        setLocalAlignment(result)
        const outputPath =
          typeof result.local_output_path === 'string' ? result.local_output_path : null
        showToast(outputPath ? `Local dataset saved: ${outputPath}` : 'Local alignment complete.')
        return String(result.alignment_run_id ?? 'local')
      } catch (error) {
        showToast(
          `Local alignment failed: ${error instanceof Error ? error.message : 'unknown error'}`,
        )
        return null
      }
    }

    const activity = activityPacket(
      attemptContextRef.current,
      params.cycle.events.map(toActivityEvent),
    )
    if (!activity?.attempt) {
      showToast('Recording context is unavailable. Please sign in and try again.')
      return null
    }
    const inputs = await getMidiInputs()
    const midiKeyboardUsed = inputs.size > 0

    const result = await saveChallengeRecording({
      schemaVersion: 2,
      songSource: source,
      songId: id,
      songTitle: songMeta?.title ?? null,
      durationSec: durationSec > 0 ? durationSec : 1,
      midiBase64: base64,
      referenceMidiBase64: params.referenceMidiBase64,
      performanceEvents: params.performanceEvents,
      activity,
      practiceSettings: {
        // `range` remains the compatibility field consumed by existing workers.
        // Both range fields make it explicit that reference.mid is only the part
        // actually observed before exit/finish.
        range: params.observedRange,
        assigned_range: params.assignedRange,
        observed_range: params.observedRange,
        reference_time_origin_sec: params.observedRange.start,
        waiting: params.referenceConfig.waiting,
        transpose: params.referenceConfig.transpose,
        time_basis: 'song_time',
        played_until_sec: params.playedUntilSec,
        left: params.referenceConfig.left,
        right: params.referenceConfig.right,
      },
      accuracyPct: accuracy,
      difficulty,
      midiKeyboardUsed,
    })

    console.log('[Challenge] saveChallengeRecording result:', result)

    if ('error' in result) {
      if (result.error !== 'Not authenticated') {
        console.error('[Challenge] Failed to save recording:', result.error)
        showToast(`Save failed: ${result.error}`)
      }
      return null
    }

    showToast('Saved on this device. Syncing in background.')
    return result.id
  }

  const handleMetronomeToggle = () => {
    const enabled = !metronome.enabled
    const nextVolume = metronome.volume ?? 0.6
    setSongConfig({
      ...songConfig,
      metronome: {
        ...metronome,
        enabled,
        volume: enabled ? nextVolume : metronome.volume,
      },
    })
  }

  type TerminalContext = {
    sessionId: string
    midiBytes: Uint8Array
    performanceEvents: PerformanceEventsAudit
    referenceMidiBase64: string
    referenceConfig: SongConfig
    observedRange: { start: number; end: number }
    assignedRange: { start: number; end: number }
    durationSec: number
    accuracy: number
    songTimeSec: number
  }

  /**
   * Closes the current play session. Everything here is synchronous: playback is frozen,
   * the recording is sealed and the session id is released before any await, so no later
   * handler can resume into a session that already ended.
   * Returns null when there is nothing to close, or when a terminal flow is already running.
   */
  const beginTerminal = (
    songTimeSec: number,
    { stopPlayback }: { stopPlayback: boolean },
  ): TerminalContext | null => {
    if (terminalFlowInFlightRef.current) return null
    const sessionId = playSessionIdRef.current
    if (!sessionId) return null

    // player.stop() resets song time, stats and the selected range, so read them first.
    const durationSec = lastDurationRef.current || ((player as any).getDuration?.() ?? 0)
    const accuracyPct = (player as any).store?.get?.((player as any).score?.accuracy) ?? 0
    const accuracy = typeof accuracyPct === 'number' ? accuracyPct : 0
    const fallbackStart = Math.max(0, songTimeSec)
    const fallbackEnd = Math.max(
      fallbackStart,
      selectedRange?.end ?? (durationSec > 0 ? durationSec : (song?.duration ?? 0)),
    )
    const assignment =
      challengeAssignmentRef.current ??
      ({
        range: { start: fallbackStart, end: fallbackEnd },
        // This fallback only occurs if the user exits during countdown before
        // recording begins. It is never sent unless a valid MIDI take exists.
        config: JSON.parse(JSON.stringify(songConfig)) as SongConfig,
      } satisfies { range: { start: number; end: number }; config: SongConfig })
    const assignedRange = assignment.range
    // Store reference.mid as precisely the score window this attempt observed.
    // It prevents a short, abandoned performance from being matched against a
    // later repeated phrase in the full song.
    const observedRange = {
      start: assignedRange.start,
      end: Math.min(Math.max(songTimeSec, assignedRange.start), assignedRange.end),
    }
    const referenceMidiBase64 = song
      ? bytesToBase64(referenceSnapshot(song, assignment.config, observedRange))
      : ''

    setTerminalFlowInFlight(true)
    pendingTransportEventRef.current = null
    playSessionIdRef.current = null
    markPlayingStopped()
    // Do not pad an abandoned performance to the original song duration. Its
    // MIDI and reference snapshot deliberately end at the same observed window.
    const recording = stopRecording(songTimeSec)
    if (stopPlayback) {
      // Flags the playing → stopped transition below as user-initiated. Only valid when we
      // actually stop the transport; setting it otherwise would swallow the next natural finish.
      pausedByUserRef.current = true
      player.stop()
    }

    return {
      sessionId,
      midiBytes: recording.midiBytes,
      performanceEvents: recording.performanceEvents,
      referenceMidiBase64,
      referenceConfig: assignment.config,
      observedRange,
      assignedRange,
      durationSec,
      accuracy,
      songTimeSec,
    }
  }

  const localSavePromiseRef = useRef<Promise<boolean> | null>(null)

  /** Seals the recording locally; the application upload queue owns remote delivery. */
  const finalizeSession = async (
    eventType: Extract<PlayEventType, 'exited' | 'finished'>,
    terminal: TerminalContext,
    extraMetadata: Record<string, unknown>,
  ): Promise<boolean> => {
    recordChallengeEvent(
      eventType,
      {
        ...extraMetadata,
        accuracy_pct: terminal.accuracy,
        ...(terminal.durationSec > 0 ? { song_duration_sec: terminal.durationSec } : {}),
      },
      terminal.songTimeSec,
      terminal.sessionId,
    )
    try {
      const recordingId = await saveChallengeRecordingFromBytes({
        sessionId: terminal.sessionId,
        midiBytes: terminal.midiBytes,
        performanceEvents: terminal.performanceEvents,
        referenceMidiBase64: terminal.referenceMidiBase64,
        referenceConfig: terminal.referenceConfig,
        observedRange: terminal.observedRange,
        assignedRange: terminal.assignedRange,
        durationSec: terminal.durationSec > 0 ? terminal.durationSec : 1,
        accuracy: terminal.accuracy,
        playedUntilSec: terminal.songTimeSec,
        cycle: { sessionId: terminal.sessionId, events: challengeEventsRef.current },
      })
      return recordingId !== null
    } finally {
      resetPlaySessionTracking()
    }
  }

  const handleExitChallenge = (reason: 'back_button' | 'confirm_exit') => {
    if (terminalFlowInFlightRef.current) {
      // A natural finish may already be sealing. Never leave before local commit.
      void localSavePromiseRef.current
        ?.then((saved) => {
          if (saved && !localAlignmentLabEnabled) navigate('/')
        })
        .catch((error) => console.error('[Challenge] Local save failed:', error))
      return
    }
    const terminal = beginTerminal(nowSongSec(), { stopPlayback: true })
    if (!terminal) {
      player.stop()
      navigate('/')
      return
    }

    // The local lab must remain on this page so its result panel can be displayed.
    if (localAlignmentLabEnabled) {
      void finalizeSession('exited', terminal, { reason }).catch((error) => {
        console.error('[Challenge] Local alignment failed:', error)
      })
      return
    }
    localSavePromiseRef.current = finalizeSession('exited', terminal, { reason })
    void localSavePromiseRef.current
      .then((saved) => {
        if (saved) navigate('/')
      })
      .catch((error) => {
        console.error('[Challenge] Save failed:', error)
        showToast('Recording could not be saved. Please keep this page open.')
      })
  }

  // ✅ 你要的：只要在播放（playing=true）就录音，即使没弹键也要录
  const handleTogglePlayingChallenge = () => {
    if (terminalFlowInFlightRef.current) {
      showToast('Saving your recording…')
      return
    }

    // A count-in is not part of a challenge take. Cancelling it must not
    // create a `paused`/`resumed` cycle without `play_started`, because that
    // would later fail the atomic finalize validation despite no recording
    // ever having begun.
    if (playerState.countingDown) {
      pendingTransportEventRef.current = null
      player.cancelCountdown()
      resetPlaySessionTracking()
      return
    }

    const isPlayingNow = playerState.playing

    if (!isPlayingNow) {
      const isNewSession = !playSessionIdRef.current
      if (isNewSession) {
        playSessionIdRef.current = crypto.randomUUID()
        challengeEventsRef.current = []
        challengeAssignmentRef.current = null
        recordedTransportStartedRef.current = false
        accumulatedPlayMsRef.current = 0
      }
      requestChallengeTransport(isNewSession ? 'play_started' : 'resumed')
    } else {
      // Pause: only pause recording and playback; show "Continue or Exit?" dialog (not the complete modal)
      pausedByUserRef.current = true
      pendingTransportEventRef.current = null
      if (recordedTransportStartedRef.current) {
        player.pause()
        const pausedSongTime = nowSongSec()
        markPlayingStopped()
        recordChallengeEvent('paused', {}, pausedSongTime)
        pauseRecording(pausedSongTime)
      } else {
        // The player may have transitioned out of the count-in just before
        // React's effect recorded `play_started`. There is still no valid
        // take, so cancel it rather than emitting a malformed cycle.
        player.pause()
        resetPlaySessionTracking()
        return
      }
      setIsConfirmExitOpen(true)
    }
  }

  useEventListener<KeyboardEvent>('keydown', (evt: KeyboardEvent) => {
    if (evt.code !== 'Space') return
    evt.preventDefault()
    // The listener is on window, so it still fires behind the end-of-challenge modal
    // and while a terminal flow is finalizing. Neither may restart playback.
    if (terminalFlowInFlightRef.current || showSuccessModal) return
    if (isConfirmExitOpen) {
      setIsConfirmExitOpen(false)
      if (!playSessionIdRef.current) {
        playSessionIdRef.current = crypto.randomUUID()
        challengeEventsRef.current = []
        accumulatedPlayMsRef.current = 0
        challengeAssignmentRef.current = null
        recordedTransportStartedRef.current = false
      }
      requestChallengeTransport(recordedTransportStartedRef.current ? 'resumed' : 'play_started')
      return
    }
    handleTogglePlayingChallenge()
  })

  // 播放过程中用 ref 记录“最后已知”的歌曲时间与总长，避免 effect 里读 player 时已被重置
  const lastSongTimeRef = useRef(0)
  const lastDurationRef = useRef(0)
  // 用户主动暂停（点 Pause 或 Exit）时置 true，避免“播放结束” effect 误当作 challenge 完成
  const pausedByUserRef = useRef(false)

  // ✅ 播放期间每 100ms 推进静默时间并更新 ref，供“播完”判断用
  useEffect(() => {
    if (playerState.countingDown || !playerState.playing || !isRecordingMidi) return
    const timer = window.setInterval(() => {
      const t = nowSongSec()
      const dur = (player as any).getDuration?.() ?? 0
      lastSongTimeRef.current = t
      if (dur > 0) lastDurationRef.current = dur
      flushSilenceTo(t)
    }, 100)
    return () => window.clearInterval(timer)
  }, [playerState.playing, playerState.countingDown, isRecordingMidi])

  // ✅ 播放结束：用 ref 判断是否播到结尾（不依赖 player 当前值），再导出并上传
  const previousPlayingRef = useRef(playerState.playing)
  useEffect(() => {
    const wasPlaying = previousPlayingRef.current
    const isPlayingNow = playerState.playing

    if (isPlayingNow) {
      const dur = (player as any).getDuration?.() ?? 0
      if (dur > 0) lastDurationRef.current = dur
    }

    if (wasPlaying && !isPlayingNow) {
      // Only treat as challenge finished when playback ended naturally (not when user paused)
      if (pausedByUserRef.current) {
        pausedByUserRef.current = false
        previousPlayingRef.current = isPlayingNow
        return
      }

      // Ignore stop transitions that happen before the user starts a challenge session
      // (e.g. preview still playing → challenge page setSong() stops player).
      // playLoop_ already paused at the end of the song, so leave the transport alone:
      // stopping here would wipe the song time and the selected range behind the modal.
      const terminal = beginTerminal(Math.max(nowSongSec(), lastSongTimeRef.current), {
        stopPlayback: false,
      })
      if (!terminal) {
        previousPlayingRef.current = isPlayingNow
        return
      }

      const succeeded = isChallengeSuccess(terminal.accuracy)
      setEndModalVariant(succeeded ? 'success' : 'complete')
      setShowSuccessModal(true)
      localSavePromiseRef.current = finalizeSession('finished', terminal, { success: succeeded })
      void localSavePromiseRef.current.catch((error) => {
        console.error('[Challenge] Local save failed:', error)
        showToast('Recording could not be saved. Please keep this page open.')
      })
    }

    previousPlayingRef.current = isPlayingNow
  }, [
    playerState.playing,
    player,
    stopRecording,
    source,
    id,
    songMeta?.title,
    songMeta?.difficulty,
  ])

  // 声音：按键就响
  useEffect(() => {
    const handleMidiEvent = ({ type, note, velocity }: MidiStateEvent) => {
      if (type === 'down') {
        synth.playNote(note, velocity)
      } else {
        synth.stopNote(note, velocity)
      }
    }

    midiState.subscribe(handleMidiEvent)
    return function cleanup() {
      midiState.unsubscribe(handleMidiEvent)
    }
  }, [synth, song, songConfig])

  if (error || (source === 'local' && !song && !isLoading)) {
    return (
      <SongNotFound
        songTitle={songMeta?.title}
        onGoBack={() => {
          player.stop()
          navigate('/songs')
        }}
      />
    )
  }

  return (
    <>
      <title>Challenge</title>
      <div
        className={clsx(
          'inset-safe fixed grid h-full min-h-0 w-full grid-rows-[auto_1fr_auto] outline-none',
        )}
        {...midiState.getListenerProps()}
        autoFocus
      >
        <TopBar
          title={songMeta?.title}
          subtitle="Challenge"
          onClickBack={() => {
            void handleExitChallenge('back_button')
          }}
          onClickMidi={() => {
            showToast('MIDI selection is disabled in challenge mode.')
          }}
          isSettingsOpen={false}
          onToggleSettings={() => {
            showToast('Settings are disabled in challenge mode.')
          }}
          onClickStats={() => setIsStatsVisible((prev) => !prev)}
          statsVisible={isStatsVisible}
        />
        <div
          className={clsx(
            'relative h-full min-h-0 min-w-0',
            songConfig.visualization === 'sheet' ? 'bg-white' : 'bg-[#0f1014]',
          )}
        >
          <SongVisualizer
            song={song}
            config={songConfig}
            hand={hand}
            handSettings={getHandSettings(songConfig)}
            selectedRange={selectedRange}
            getTime={() => (player as any).getTime?.() ?? 0}
            enableTouchscroll={false}
          />
          {playerState.countingDown && countdownTotal > 0 && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <CountdownOverlay total={countdownTotal} remaining={countdownRemaining} />
            </div>
          )}
        </div>
        <div className="flex h-12 min-h-12 shrink-0 items-center justify-between border-t border-[#23242b] bg-[#141419] px-4 pb-[env(safe-area-inset-bottom)] text-gray-200">
          <div className="flex items-center gap-3">
            <button
              className="flex h-9 items-center justify-center rounded-full bg-violet-600 px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
              onClick={handleTogglePlayingChallenge}
              disabled={isFinalizing}
            >
              {isFinalizing
                ? 'Saving…'
                : playerState.playing
                  ? 'Pause Challenge'
                  : 'Start Challenge'}
            </button>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex flex-col items-end select-none">
              <div className="flex items-baseline gap-1 font-mono select-none">
                <span className="text-sm font-semibold text-white select-none">{currentTime}</span>
                <span className="text-[11px] text-gray-500 select-none">/ {duration}</span>
              </div>
            </div>
            <button className="text-xs text-gray-300" onClick={handleMetronomeToggle}>
              {metronome.enabled ? 'Metronome: ON' : 'Metronome: OFF'}
            </button>
          </div>
        </div>
      </div>

      {isConfirmExitOpen && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50">
          <div className="w-80 rounded-lg bg-[#1b1c23] p-6 text-sm text-white">
            <div className="mb-4 font-semibold">Exit challenge?</div>
            <div className="flex justify-end gap-3">
              <button
                className="rounded border border-gray-500 px-3 py-1.5 text-xs"
                onClick={() => {
                  if (terminalFlowInFlightRef.current) return
                  setIsConfirmExitOpen(false)
                  if (!playSessionIdRef.current) {
                    playSessionIdRef.current = crypto.randomUUID()
                    challengeEventsRef.current = []
                    accumulatedPlayMsRef.current = 0
                    challengeAssignmentRef.current = null
                    recordedTransportStartedRef.current = false
                  }
                  requestChallengeTransport(
                    recordedTransportStartedRef.current ? 'resumed' : 'play_started',
                  )
                }}
              >
                Continue
              </button>
              <button
                className="rounded bg-red-600 px-3 py-1.5 text-xs"
                onClick={() => {
                  setIsConfirmExitOpen(false)
                  void handleExitChallenge('confirm_exit')
                }}
              >
                Exit
              </button>
            </div>
          </div>
        </div>
      )}
      {localAlignment && (
        <div className="fixed inset-0 z-50 overflow-auto bg-black/70 p-6">
          <div className="mx-auto max-w-4xl rounded-lg bg-white p-5 text-slate-900">
            <div className="flex items-center justify-between gap-4">
              <div>
                <h2 className="text-lg font-bold">Local alignment result</h2>
                <p className="text-sm text-slate-600">
                  Production align() ran locally. Result saved to the local alignment output
                  directory.
                </p>
              </div>
              <button
                className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white"
                onClick={() => setLocalAlignment(null)}
              >
                Close
              </button>
            </div>
            <LocalAlignmentTimeline result={localAlignment} />
            <div className="mt-4 max-h-[65vh] overflow-auto rounded bg-slate-950 p-3 font-mono text-xs text-slate-100">
              {(localAlignment.notes ?? []).map((note: any, index: number) => (
                <div key={index}>
                  {note.alignment_type} · R {note.ref_pitch ?? '–'} → P{' '}
                  {note.performance_pitch ?? '–'} ·{' '}
                  {note.raw_timing_deviation_sec == null
                    ? ''
                    : `${(note.raw_timing_deviation_sec * 1000).toFixed(0)} ms`}{' '}
                  · {note.is_correct ? 'correct' : `reject ${note.reject_reason}`}
                </div>
              ))}
            </div>
            <h3 className="mt-5 font-semibold">events_data: hand-level aggregation</h3>
            <div className="mt-2 grid gap-3 md:grid-cols-2">
              {(['left_hand', 'right_hand'] as const).map((hand, handIndex) => {
                const events = localAlignment.events_data
                return (
                  <div className="rounded border border-slate-200 p-3 text-xs" key={hand}>
                    <b>{hand}</b>
                    {(events?.onset_seconds ?? []).map((time: number, index: number) => (
                      <div className="mt-1 font-mono" key={index}>
                        {time}s · pitches {(events.pitches[index]?.[handIndex] ?? []).join(',')} ·
                        reject {(events.reject_reason.note[index]?.[handIndex] ?? []).join(',')} ·
                        timing {(events.timing_offset_ms[index]?.[handIndex] ?? []).join(',')}ms
                      </div>
                    ))}
                  </div>
                )
              })}
            </div>
            <button
              className="mt-4 rounded border border-slate-400 px-3 py-1.5 text-sm"
              onClick={() => {
                const blob = new Blob([JSON.stringify(localAlignment, null, 2)], {
                  type: 'application/json',
                })
                const url = URL.createObjectURL(blob)
                const link = document.createElement('a')
                link.href = url
                link.download = 'local-alignment-result.json'
                link.click()
                URL.revokeObjectURL(url)
              }}
            >
              Download real result.json
            </button>
          </div>
        </div>
      )}

      {isStatsVisible && <StatsPopup />}

      <ChallengeSuccessModal
        show={showSuccessModal}
        isSaving={isFinalizing}
        onClose={() => setShowSuccessModal(false)}
        variant={endModalVariant}
      />

      <Toast
        open={!!toastMsg}
        onOpenChange={hideToast}
        title={toastMsg ? toastMsg : ''}
        toastKey={toastKey}
      />
      <RadixToast.Viewport className="fixed right-4 bottom-4 z-50 flex w-80 max-w-[100vw] flex-col-reverse gap-3 p-4" />
    </>
  )
}
