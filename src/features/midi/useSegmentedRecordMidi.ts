import { useEffect, useMemo, useRef, useState } from 'react'
import type { MidiStateEvent } from '@/types'

/**
 * 一个非常轻量、自包含的 MIDI Writer：
 * - 单轨 (format 0)
 * - PPQ = 480
 * - 默认 tempo = 120bpm（可改）
 *
 * 关键点：我们维护 track 的“当前 tick”，并允许 addSilenceTicks() 来推进时间轴，
 * 即使没有 note 事件也能让 MIDI 变长 —— 这正是你要的“只要播放就录音”。
 */

const PPQ = 480
const DEFAULT_BPM = 120

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n))
}

function secToTicks(sec: number, bpm = DEFAULT_BPM) {
  // ticks = sec * beats/sec * ticks/beat
  // beats/sec = bpm / 60
  return Math.round(sec * (bpm / 60) * PPQ)
}

// Variable Length Quantity (VLQ) encoding
function encodeVLQ(value: number): number[] {
  let v = value >>> 0
  const bytes: number[] = [v & 0x7f]
  v >>>= 7
  while (v > 0) {
    bytes.unshift((v & 0x7f) | 0x80)
    v >>>= 7
  }
  return bytes
}

function u16be(n: number) {
  return [(n >> 8) & 0xff, n & 0xff]
}

function u32be(n: number) {
  return [(n >> 24) & 0xff, (n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff]
}

function strAscii(s: string) {
  const out: number[] = []
  for (let i = 0; i < s.length; i++) out.push(s.charCodeAt(i) & 0x7f)
  return out
}

type TrackEvent = {
  deltaTicks: number
  bytes: number[] // raw midi event bytes (not including delta)
}

class SimpleMidiRecorder {
  private bpm: number
  private events: TrackEvent[] = []
  private currentTick = 0
  private started = false

  constructor(bpm = DEFAULT_BPM) {
    this.bpm = bpm
  }

  start() {
    this.events = []
    this.currentTick = 0
    this.started = true

    // Tempo meta event: 0xFF 0x51 0x03 tt tt tt (microseconds per quarter note)
    // usPerQuarter = 60_000_000 / bpm
    const usPerQuarter = Math.round(60000000 / this.bpm)
    const t1 = (usPerQuarter >> 16) & 0xff
    const t2 = (usPerQuarter >> 8) & 0xff
    const t3 = usPerQuarter & 0xff
    this.events.push({
      deltaTicks: 0,
      bytes: [0xff, 0x51, 0x03, t1, t2, t3],
    })

    // Time Signature (optional): 4/4
    this.events.push({
      deltaTicks: 0,
      bytes: [0xff, 0x58, 0x04, 0x04, 0x02, 0x18, 0x08],
    })
  }

  /**
   * 推进时间轴（静默），不写任何音符，只是把后续事件的 delta-time 变大。
   */
  addSilenceTicks(deltaTicks: number) {
    if (!this.started) return
    if (deltaTicks <= 0) return
    this.currentTick += deltaTicks
    // 注意：这里不立即写事件，只是推进“当前时间”。
    // 真正写事件时用 (eventTick - lastEventTick) 来算 deltaTicks。
    // 为了简单，我们把“静默”作为一个 Marker meta event 写进去推进时间：
    // 0xFF 0x06 len text...
    this.events.push({
      deltaTicks,
      bytes: [0xff, 0x06, ...encodeVLQ(1), 0x2e], // "." 作为 marker 文本
    })
  }

  noteOn(deltaTicks: number, note: number, velocity: number, channel = 0) {
    if (!this.started) return
    const n = clamp(note, 0, 127)
    const v = clamp(Math.round(velocity * 127), 0, 127)
    this.events.push({
      deltaTicks,
      bytes: [0x90 | (channel & 0x0f), n, v],
    })
  }

  noteOff(deltaTicks: number, note: number, velocity: number, channel = 0) {
    if (!this.started) return
    const n = clamp(note, 0, 127)
    const v = clamp(Math.round(velocity * 127), 0, 127)
    this.events.push({
      deltaTicks,
      bytes: [0x80 | (channel & 0x0f), n, v],
    })
  }

  /**
   * finalize: 写 End Of Track
   */
  finish(): Uint8Array {
    if (!this.started) return new Uint8Array()

    // End of track
    this.events.push({
      deltaTicks: 0,
      bytes: [0xff, 0x2f, 0x00],
    })

    // Build track bytes
    const trackData: number[] = []
    for (const ev of this.events) {
      trackData.push(...encodeVLQ(ev.deltaTicks))
      trackData.push(...ev.bytes)
    }

    // Header chunk: MThd len=6 format=0 ntrks=1 division=PPQ
    const header: number[] = [
      ...strAscii('MThd'),
      ...u32be(6),
      ...u16be(0),
      ...u16be(1),
      ...u16be(PPQ),
    ]

    // Track chunk: MTrk len=trackData.length
    const track: number[] = [
      ...strAscii('MTrk'),
      ...u32be(trackData.length),
      ...trackData,
    ]

    this.started = false
    return new Uint8Array([...header, ...track])
  }
}

type MidiStateLike = {
  subscribe: (fn: (e: MidiStateEvent) => void) => void
  unsubscribe: (fn: (e: MidiStateEvent) => void) => void
}

/**
 * ✅ 你要的核心：用“歌曲时间 songTimeSec”驱动时间轴，而不是按键事件驱动。
 *
 * 我们维护 lastSongTimeSec：
 * - start/resume/pause/stop/flush 时调用 advanceTo(songTimeSec)
 * - advanceTo 会计算 deltaSec，转成 deltaTicks，并写入“静默 Marker”事件推进 MIDI 时间轴
 *
 * 然后按键事件到来时：
 * - 先 advanceTo(事件发生时刻的 songTimeSec)（通常在事件回调里读 player.getTime()）
 * - 再写 noteOn/noteOff（deltaTicks=0，因为时间已经推进到位）
 *
 * ⚠️ 因为 hook 里拿不到 player，所以我提供了 flushSilenceTo(songTimeSec)
 *    由页面在播放时定时调用（100ms一次就够）来保证即使没按键也一直推进时间轴。
 */
export function useSegmentedRecordMidi(midiState: MidiStateLike) {
  const [isRecording, setIsRecording] = useState(false)

  const recorderRef = useRef<SimpleMidiRecorder | null>(null)
  const lastSongTimeSecRef = useRef<number | null>(null)

  // 页面传入 songTimeSec，这里补齐静默
  const advanceTo = (songTimeSec: number) => {
    if (!recorderRef.current) return
    const last = lastSongTimeSecRef.current
    if (last == null) {
      lastSongTimeSecRef.current = songTimeSec
      return
    }
    const deltaSec = songTimeSec - last
    if (deltaSec <= 0) return
    const deltaTicks = secToTicks(deltaSec, DEFAULT_BPM)
    if (deltaTicks > 0) {
      recorderRef.current.addSilenceTicks(deltaTicks)
    }
    lastSongTimeSecRef.current = songTimeSec
  }

  const startOrResumeRecording = (songTimeSec: number) => {
    if (!recorderRef.current) {
      recorderRef.current = new SimpleMidiRecorder(DEFAULT_BPM)
      recorderRef.current.start()
      lastSongTimeSecRef.current = songTimeSec
      setIsRecording(true)
      return
    }
    // resume: 对齐时间（补静默到 resume 时刻）
    advanceTo(songTimeSec)
    setIsRecording(true)
  }

  const pauseRecording = (songTimeSec: number) => {
    if (!recorderRef.current) return
    // pause 时也要补齐静默到 pause 的时刻
    advanceTo(songTimeSec)
    setIsRecording(false)
  }

  /**
   * @param songTimeSec 当前歌曲时间（秒），用于补齐静默到此刻
   * @param targetDurationSec 可选：原曲总时长（秒）。若传入，会再补齐静默到该时长，使导出的 MIDI 和原曲时长一致
   */
  const stopRecording = (songTimeSec: number, targetDurationSec?: number) => {
    if (!recorderRef.current) return new Uint8Array()
    // stop 前补齐静默到当前时刻
    advanceTo(songTimeSec)
    // 若传入原曲总时长，再补齐到该时长，保证录音总长和原曲一致
    if (typeof targetDurationSec === 'number' && targetDurationSec > (lastSongTimeSecRef.current ?? 0)) {
      advanceTo(targetDurationSec)
    }
    const bytes = recorderRef.current.finish()
    recorderRef.current = null
    lastSongTimeSecRef.current = null
    setIsRecording(false)
    return bytes
  }

  // 播放中定时调用：保证“没按键也在录”
  const flushSilenceTo = (songTimeSec: number) => {
    if (!recorderRef.current) return
    if (!isRecording) return
    advanceTo(songTimeSec)
  }

  // 监听 midiState：按键事件写入 MIDI（注意：事件本身不推进时间，时间靠 flushSilenceTo / pause/stop 推进）
  useEffect(() => {
    const handler = (e: MidiStateEvent) => {
      if (!recorderRef.current) return
      if (!isRecording) return

      // 这里 deltaTicks 用 0，因为时间轴靠 advanceTo() 推进了。
      // 只要页面在播放中不断 flushSilenceTo(songTimeSec)，事件会落在正确的时间位置附近。
      const note = (e as any).note ?? 60
      const vel = (e as any).velocity ?? 0.8

      if (e.type === 'down') {
        recorderRef.current.noteOn(0, note, vel)
      } else {
        recorderRef.current.noteOff(0, note, vel)
      }
    }

    midiState.subscribe(handler)
    return () => midiState.unsubscribe(handler)
  }, [midiState, isRecording])

  return useMemo(
    () => ({
      isRecording,
      startOrResumeRecording,
      pauseRecording,
      stopRecording,
      flushSilenceTo,
    }),
    [isRecording],
  )
}