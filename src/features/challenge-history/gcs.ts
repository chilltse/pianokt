import type { Activity } from '@/features/activity'
import { supabase } from '@/features/auth/supabase'
import { indexedUploadStore, UploadQueue, type PendingUpload } from './upload-queue'

const apiRoot = (import.meta.env.VITE_PIANOKT_API_URL as string | undefined)?.replace(/\/$/, '')
export const localAlignmentLabEnabled =
  import.meta.env.DEV && import.meta.env.VITE_LOCAL_ALIGNMENT_LAB === 'true'
export async function backendRequest<T>(
  path: string,
  body?: unknown,
  expectedOwner?: string,
): Promise<T> {
  if (!apiRoot || !supabase) throw new Error('PianoKT backend is not configured')
  const {
    data: { session },
  } = await supabase.auth.getSession()
  if (!session) throw new Error('Not authenticated')
  if (expectedOwner && session.user.id !== expectedOwner)
    throw new Error('Upload belongs to another signed-in user')
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

export async function runLocalAlignment(params: {
  performanceMidiBase64: string
  referenceMidiBase64: string
  performanceEvents: RecordingUpload['performanceEvents']
  observedEndSec: number
  referenceOriginSec: number
}): Promise<Record<string, unknown>> {
  if (!localAlignmentLabEnabled || !apiRoot) throw new Error('Local alignment lab is disabled')
  const response = await fetch(`${apiRoot}/debug/local-align`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      performance_midi_base64: params.performanceMidiBase64,
      reference_midi_base64: params.referenceMidiBase64,
      performance_events: params.performanceEvents,
      observed_end_sec: params.observedEndSec,
      reference_origin_sec: params.referenceOriginSec,
    }),
  })
  if (!response.ok) throw new Error(`Local alignment ${response.status}: ${await response.text()}`)
  return response.json() as Promise<Record<string, unknown>>
}
export type RecordingUpload = {
  songSource: string
  songId: string
  songTitle: string | null
  durationSec: number
  midiBase64: string
  referenceMidiBase64: string
  sessionId?: string
  activity?: Activity
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
async function uploadSealedRecording(
  params: RecordingUpload,
  ownerId: string,
  attemptId: string,
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
    const urls = await backendRequest<{
      performance_url: string
      reference_url: string
      performance_events_url?: string
    }>(
      '/attempts',
      {
        attempt_id: attemptId,
        song_id: params.songId,
        song_source: params.songSource,
        song_title: params.songTitle,
        duration_sec: params.durationSec,
        performance_hash: performanceHash,
        reference_hash: referenceHash,
        session_id: params.sessionId ?? null,
        activity: params.activity,
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
      },
      ownerId,
    )
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
    const result = await backendRequest<{ id: string }>(
      `/attempts/${attemptId}/finalize`,
      {
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
      },
      ownerId,
    )
    return result
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Upload failed' }
  }
}

// Storage quota/availability errors retain the full bundle in memory for retry.
// Browser close cannot guarantee durability until IndexedDB reports a commit.
const unsealed = new Map<string, PendingUpload<RecordingUpload>>()
function protectUnsealed(event: BeforeUnloadEvent) {
  if (unsealed.size) {
    event.preventDefault()
    event.returnValue = ''
  }
}
function sealed(id: string) {
  unsealed.delete(id)
  if (!unsealed.size) window.removeEventListener('beforeunload', protectUnsealed)
}
let queue: UploadQueue<RecordingUpload> | undefined
function uploadQueue() {
  return (queue ??= new UploadQueue(indexedUploadStore<RecordingUpload>(), async (record) => {
    const result = await uploadSealedRecording(record.payload, record.ownerId, record.id)
    if ('error' in result) throw new Error(result.error)
  }))
}

export async function saveGcsRecording(
  params: RecordingUpload,
): Promise<{ id: string } | { error: string }> {
  try {
    const ownerId =
      params.activity?.owner_id ?? (await supabase?.auth.getSession())?.data.session?.user.id
    if (!ownerId) throw new Error('Not authenticated')
    const id = params.activity?.attempt?.attempt_id ?? params.cycle.sessionId
    // Serialization seals values, including telemetry and the final event sequence.
    const payload = JSON.parse(JSON.stringify(params)) as RecordingUpload
    const record = { id, ownerId, payload }
    const key = `${ownerId}:${id}`
    if (unsealed.has(key) && JSON.stringify(unsealed.get(key)) !== JSON.stringify(record))
      throw new Error('Immutable upload bundle conflict')
    unsealed.set(key, record)
    window.addEventListener('beforeunload', protectUnsealed)
    await uploadQueue().enqueue(record, () => sealed(key))
    return { id }
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Upload failed' }
  }
}

let replaying = false
export async function resumePendingUploads(ownerId: string) {
  if (replaying || !apiRoot || localAlignmentLabEnabled) return
  replaying = true
  try {
    for (const [key, record] of unsealed) {
      if (record.ownerId !== ownerId) continue
      try {
        await uploadQueue().enqueue(record, () => sealed(key))
      } catch (error) {
        console.warn('[Upload recovery] Retry pending', error)
      }
    }
    const failures = await uploadQueue().replay(ownerId)
    if (failures.length) console.warn('[Upload recovery] Bundles retained for retry', failures)
  } catch (error) {
    console.error('[Upload recovery] Persistence unavailable', error)
  } finally {
    replaying = false
  }
}
