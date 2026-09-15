import { supabase } from '@/features/auth/supabase'

const apiRoot = (import.meta.env.VITE_PIANOKT_API_URL as string | undefined)?.replace(/\/$/, '')
export async function backendRequest<T>(path: string, body?: unknown): Promise<T> {
  if (!apiRoot || !supabase) throw new Error('PianoKT backend is not configured')
  const {
    data: { session },
  } = await supabase.auth.getSession()
  if (!session) throw new Error('Not authenticated')
  const response = await fetch(apiRoot + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      Authorization: `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`Backend ${response.status}: ${await response.text()}`)
  return response.json() as Promise<T>
}
export type RecordingUpload = {
  songSource: string
  songId: string
  songTitle: string | null
  durationSec: number
  midiBase64: string
  referenceMidiBase64: string
  sessionId?: string
  practiceSettings?: Record<string, unknown>
  accuracyPct?: number
  difficulty?: number
  midiKeyboardUsed?: boolean
  /** Immutable, per-key evidence used by the time-guided alignment worker. */
  performanceEvents?: {
    schemaVersion: 1
    events: Array<{
      sequence: number
      type: 'note_on' | 'note_off'
      midiEventTimestamp: number
      playerSongTime: number
      performanceNowTimestamp: number
      pitch: number
      velocity: number
      timestampSource: 'web_midi' | 'browser_keyboard'
    }>
    transportSegments: Array<{
      sequence: number
      type: 'start' | 'resume' | 'pause' | 'stop'
      playerSongTime: number
      performanceNowTimestamp: number
    }>
  }
  cycle: {
    sessionId: string
    events: Array<{
      eventId: string
      eventType: 'play_started' | 'paused' | 'resumed' | 'finished' | 'exited'
      songTimeSec: number
      clientTs: string
      metadata: Record<string, unknown>
    }>
  }
}
function decode(s: string) {
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0))
}
async function hash(bytes: Uint8Array) {
  const value = await crypto.subtle.digest('SHA-256', new Uint8Array(bytes))
  return Array.from(new Uint8Array(value), (b) => b.toString(16).padStart(2, '0')).join('')
}
export async function saveGcsRecording(
  params: RecordingUpload,
): Promise<{ id: string } | { error: string }> {
  try {
    const performance = decode(params.midiBase64),
      reference = decode(params.referenceMidiBase64)
    const performanceHash = await hash(performance),
      referenceHash = await hash(reference)
    const performanceEvents = params.performanceEvents
    const performanceEventsBytes = performanceEvents
      ? new TextEncoder().encode(JSON.stringify(performanceEvents))
      : undefined
    const performanceEventsHash = performanceEventsBytes
      ? await hash(performanceEventsBytes)
      : undefined
    const key = `pianokt-upload:${params.sessionId ?? params.songId}:${performanceHash}:${referenceHash}:${performanceEventsHash ?? 'legacy'}`
    const attemptId = sessionStorage.getItem(key) ?? crypto.randomUUID()
    sessionStorage.setItem(key, attemptId)
    const urls = await backendRequest<{
      performance_url: string
      reference_url: string
      performance_events_url?: string
    }>('/attempts', {
      attempt_id: attemptId,
      song_id: params.songId,
      song_source: params.songSource,
      song_title: params.songTitle,
      duration_sec: params.durationSec,
      performance_hash: performanceHash,
      reference_hash: referenceHash,
      session_id: params.sessionId ?? null,
      settings: {
        ...params.practiceSettings,
        client_accuracy_pct: params.accuracyPct,
        client_difficulty: params.difficulty,
        midi_keyboard_used: params.midiKeyboardUsed,
      },
      ...(performanceEventsHash
        ? {
            performance_events_hash: performanceEventsHash,
            performance_events_count: performanceEvents?.events.length,
          }
        : {}),
    })
    const uploads: Array<{ url: string; bytes: Uint8Array; contentType: string }> = [
      { url: urls.performance_url, bytes: performance, contentType: 'audio/midi' },
      { url: urls.reference_url, bytes: reference, contentType: 'audio/midi' },
    ]
    if (performanceEventsBytes) {
      if (!urls.performance_events_url)
        throw new Error('Backend did not provide a performance event upload URL')
      uploads.push({
        url: urls.performance_events_url,
        bytes: performanceEventsBytes,
        contentType: 'application/json',
      })
    }
    for (const { url, bytes, contentType } of uploads) {
      const response = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': contentType, 'x-goog-if-generation-match': '0' },
        body: new Uint8Array(bytes),
      })
      if (!response.ok && response.status !== 412)
        throw new Error(`MIDI upload failed: ${response.status}`)
    }
    const result = await backendRequest<{ id: string }>(`/attempts/${attemptId}/finalize`, {
      cycle: {
        session_id: params.cycle.sessionId,
        events: params.cycle.events.map((event) => ({
          event_id: event.eventId,
          event_type: event.eventType,
          song_time_sec: event.songTimeSec,
          client_ts: event.clientTs,
          metadata: event.metadata,
        })),
      },
    })
    sessionStorage.removeItem(key)
    return result
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Upload failed' }
  }
}
