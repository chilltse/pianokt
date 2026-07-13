import Toast from '@/components/Toast'
import {
  isChallengeSuccess,
  logPlayEvent,
  uploadChallengeSessionMidi,
} from '@/features/challenge-history'
import type { PlayEventType } from '@/features/challenge-history'
import { useSong } from '@/features/data'
import { useSongMetadata } from '@/features/data/library'
import midiState, { getMidiInputs, useSegmentedRecordMidi } from '@/features/midi'
import { useSongScrubTimes } from '@/features/controls'
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
import { MidiStateEvent, SongSource } from '@/types'
import { bytesToBase64 } from '@/utils'
import * as RadixToast from '@radix-ui/react-toast'
import clsx from 'clsx'
import { useAtomValue } from 'jotai'
import { AlertCircle, ArrowLeft } from 'lucide-react'
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { TopBar } from '@/pages/play/components'
import CountdownOverlay from '@/pages/play/components/CountdownOverlay'
import { StatsPopup } from '@/pages/play/components/StatsPopup'
import ChallengeSuccessModal, { type ChallengeEndVariant } from './ChallengeSuccessModal'

function SongNotFound({ songTitle, onGoBack }: { songTitle?: string; onGoBack: () => void }) {
  return (
    <div className="flex h-screen items-center justify-center bg-paper bg-amber-50/70">
      <div className="mx-auto max-w-md rounded-lg bg-white p-6 text-center shadow-lg border border-amber-100">
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
  const navigate = useNavigate()
  let { source, id }: { source: SongSource; id: string } =
    Object.fromEntries(searchParams) as any

  if (!source || !id) {
    navigate('/', { replace: true })
    return null
  }
  id = decodeURIComponent(id)

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
  const [toastKey, setToastKey] = useState<string>('')
  const toastKeyRef = useRef(toastKey)
  const playSessionIdRef = useRef<string | null>(null)
  const playStartedAtMsRef = useRef<number | null>(null)
  const accumulatedPlayMsRef = useRef(0)
  const { currentTime, duration } = useSongScrubTimes()

  const range = useAtomValue(player.getRange())
  const selectedRange = useMemo(
    () => (range ? { start: range[0], end: range[1] } : undefined),
    [range],
  )

  useWakeLock()
  useOnUnmount(() => player.stop())

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

  const metronome =
    songConfig.metronome ?? getDefaultSongSettings(song ?? undefined).metronome
  const countdownEnabled =
    songConfig.countdownEnabled ??
    getDefaultSongSettings(song ?? undefined).countdownEnabled
  const transpose =
    songConfig.transpose ??
    getDefaultSongSettings(song ?? undefined).transpose

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
  const nowSongSec = () => {
    // 你项目里 player.getTime() / player.currentSongTime 可能是秒
    // 我这里优先用 getTime()，没有的话再读 currentSongTime
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
  } = useSegmentedRecordMidi(midiState)

  function showToast(msg: string) {
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
    const dur = (player as any).getDuration?.() ?? lastDurationRef.current ?? 0
    return typeof dur === 'number' && Number.isFinite(dur) && dur > 0 ? dur : 0
  }

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

  const resetPlaySessionTracking = () => {
    playSessionIdRef.current = null
    playStartedAtMsRef.current = null
    accumulatedPlayMsRef.current = 0
  }

  const emitPlayEvent = (eventType: PlayEventType, metadata: Record<string, unknown> = {}) => {
    const sessionId = playSessionIdRef.current
    if (!sessionId) return

    logPlayEvent({
      sessionId,
      songId: id,
      exerciseId: `challenge:${id}`,
      playMode: 'challenge',
      eventType,
      songTimeSec: nowSongSec(),
      metadata: {
        song_source: source,
        song_title: songMeta?.title ?? null,
        song_duration_sec: getSongDurationSec(),
        time_playing_sec: Number(getTimePlayingSec().toFixed(3)),
        difficulty: songMeta?.difficulty ?? 0,
        ...metadata,
      },
    }).then((result) => {
      if ('error' in result && result.error !== 'Not authenticated') {
        console.warn(`[challenge-events] failed to log ${eventType}:`, result.error)
      }
    }).catch((error) => {
      console.warn(`[challenge-events] failed to log ${eventType}:`, error)
    })
  }

  const getAccuracyPct = () => {
    const accuracyPct = (player as any).store?.get?.((player as any).score?.accuracy) ?? 0
    return typeof accuracyPct === 'number' ? accuracyPct : 0
  }

  /** Challenge 结束：上传 MIDI、写终止事件并聚合到 user_play_logs。 */
  const endChallengeSession = async ({
    eventType,
    extraMetadata = {},
    showModal = false,
    navigateTo,
  }: {
    eventType: 'finished' | 'exited'
    extraMetadata?: Record<string, unknown>
    showModal?: boolean
    navigateTo?: string
  }) => {
    const songTime = lastSongTimeRef.current || nowSongSec()
    const dur = lastDurationRef.current || getSongDurationSec()
    const midiBytes = stopRecording(songTime, dur > 0 ? dur : undefined)
    const accuracy = getAccuracyPct()
    const sessionId = playSessionIdRef.current

    markPlayingStopped()

    let midiStoragePath: string | null = null
    if (midiBytes && midiBytes.length > 0 && sessionId) {
      const uploadResult = await uploadChallengeSessionMidi({
        sessionId,
        midiBase64: bytesToBase64(midiBytes),
      })
      if ('path' in uploadResult) {
        midiStoragePath = uploadResult.path
      } else if (uploadResult.error !== 'Not authenticated') {
        console.error('[Challenge] Failed to upload recording:', uploadResult.error)
        showToast(`Save failed: ${uploadResult.error}`)
      }
    }

    const inputs = await getMidiInputs()

    if (sessionId) {
      const result = await logPlayEvent({
        sessionId,
        songId: id,
        exerciseId: `challenge:${id}`,
        playMode: 'challenge',
        eventType,
        songTimeSec: songTime,
        metadata: {
          song_source: source,
          song_title: songMeta?.title ?? null,
          song_duration_sec: dur > 0 ? dur : getSongDurationSec(),
          time_playing_sec: Number(getTimePlayingSec().toFixed(3)),
          accuracy_pct: accuracy,
          difficulty: songMeta?.difficulty ?? 0,
          midi_keyboard_used: inputs.size > 0,
          midi_storage_path: midiStoragePath,
          ...(eventType === 'finished'
            ? { success: isChallengeSuccess(accuracy) }
            : {}),
          ...extraMetadata,
        },
      })
      if ('error' in result && result.error !== 'Not authenticated') {
        console.warn(`[challenge-events] failed to log ${eventType}:`, result.error)
      }
    }

    resetPlaySessionTracking()

    if (showModal) {
      const succeeded = isChallengeSuccess(accuracy)
      setEndModalVariant(succeeded ? 'success' : 'complete')
      setShowSuccessModal(true)
      if (midiStoragePath) {
        showToast('Recording saved.')
      }
    }

    if (navigateTo) {
      navigate(navigateTo)
    }
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

  // ✅ 你要的：只要在播放（playing=true）就录音，即使没弹键也要录
  const handleTogglePlayingChallenge = () => {
    const isPlayingNow = playerState.playing

    if (!isPlayingNow) {
      const isNewSession = !playSessionIdRef.current
      if (isNewSession) {
        playSessionIdRef.current = crypto.randomUUID()
        accumulatedPlayMsRef.current = 0
      }
      markPlayingStarted()
      emitPlayEvent(isNewSession ? 'play_started' : 'resumed')
      // Start
      startOrResumeRecording(nowSongSec())
      player.play()
    } else {
      // Pause: only pause recording and playback; show "Continue or Exit?" dialog (not the complete modal)
      pausedByUserRef.current = true
      markPlayingStopped()
      emitPlayEvent('paused')
      pauseRecording(nowSongSec())
      player.pause()
      setIsConfirmExitOpen(true)
    }
  }

  useEventListener<KeyboardEvent>('keydown', (evt: KeyboardEvent) => {
    if (evt.code !== 'Space') return
    evt.preventDefault()
    if (isConfirmExitOpen) {
      setIsConfirmExitOpen(false)
      if (!playSessionIdRef.current) {
        playSessionIdRef.current = crypto.randomUUID()
        accumulatedPlayMsRef.current = 0
        emitPlayEvent('play_started')
      } else {
        emitPlayEvent('resumed')
      }
      markPlayingStarted()
      startOrResumeRecording(nowSongSec())
      player.play()
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
    if (!playerState.playing || !isRecordingMidi) return
    const timer = window.setInterval(() => {
      const t = nowSongSec()
      const dur = (player as any).getDuration?.() ?? 0
      lastSongTimeRef.current = t
      if (dur > 0) lastDurationRef.current = dur
      flushSilenceTo(t)
    }, 100)
    return () => window.clearInterval(timer)
  }, [playerState.playing, isRecordingMidi])

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

      void endChallengeSession({
        eventType: 'finished',
        showModal: true,
      })
    }

    previousPlayingRef.current = isPlayingNow
  }, [playerState.playing, player, stopRecording, source, id, songMeta?.title, songMeta?.difficulty])

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
        className={clsx('fixed inset-safe grid h-full w-full grid-rows-[auto_1fr_auto] outline-none min-h-0')}
        {...midiState.getListenerProps()}
        autoFocus
      >
        <TopBar
          title={songMeta?.title}
          subtitle="Challenge"
          onClickBack={() => {
            pausedByUserRef.current = true
            player.stop()
            void endChallengeSession({
              eventType: 'exited',
              extraMetadata: { reason: 'back_button' },
              navigateTo: '/',
            })
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
              className="flex h-9 px-4 items-center justify-center rounded-full bg-violet-600 text-white text-sm font-semibold"
              onClick={handleTogglePlayingChallenge}
            >
              {playerState.playing ? 'Pause Challenge' : 'Start Challenge'}
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
          <div className="rounded-lg bg-[#1b1c23] p-6 w-80 text-sm text-white">
            <div className="mb-4 font-semibold">Exit challenge?</div>
            <div className="flex justify-end gap-3">
              <button
                className="px-3 py-1.5 text-xs rounded border border-gray-500"
                onClick={() => {
                  setIsConfirmExitOpen(false)
                  markPlayingStarted()
                  startOrResumeRecording(nowSongSec())
                  player.play()
                }}
              >
                Continue
              </button>
              <button
                className="px-3 py-1.5 text-xs rounded bg-red-600"
                onClick={() => {
                  pausedByUserRef.current = true
                  player.stop()
                  setIsConfirmExitOpen(false)
                  void endChallengeSession({
                    eventType: 'exited',
                    extraMetadata: { reason: 'confirm_exit' },
                    navigateTo: '/',
                  })
                }}
              >
                Exit
              </button>
            </div>
          </div>
        </div>
      )}

      {isStatsVisible && <StatsPopup />}

      <ChallengeSuccessModal
        show={showSuccessModal}
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