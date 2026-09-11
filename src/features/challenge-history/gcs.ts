import { supabase } from '@/features/auth/supabase'
const apiRoot = (import.meta.env.VITE_PIANOKT_API_URL as string | undefined)?.replace(/\/$/, '')
export async function backendRequest<T>(path: string, body?: unknown): Promise<T> {
  if (!apiRoot || !supabase) throw new Error('PianoKT backend is not configured')
  const { data: { session } } = await supabase.auth.getSession()
  if (!session) throw new Error('Not authenticated')
  const response = await fetch(apiRoot + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`Backend ${response.status}: ${await response.text()}`)
  return response.json() as Promise<T>
}
export type RecordingUpload = {
  songSource: string; songId: string; songTitle: string | null; durationSec: number;
  midiBase64: string; referenceMidiBase64: string; sessionId?: string;
  practiceSettings?: Record<string, unknown>; accuracyPct?: number; difficulty?: number; midiKeyboardUsed?: boolean;
}
function decode(s: string) { return Uint8Array.from(atob(s), c => c.charCodeAt(0)) }
async function hash(bytes: Uint8Array) {
  const value = await crypto.subtle.digest('SHA-256', new Uint8Array(bytes))
  return Array.from(new Uint8Array(value), b => b.toString(16).padStart(2, '0')).join('')
}
export async function saveGcsRecording(params: RecordingUpload): Promise<{ id: string } | { error: string }> {
  try {
    const performance = decode(params.midiBase64), reference = decode(params.referenceMidiBase64)
    const performanceHash = await hash(performance), referenceHash = await hash(reference)
    const key = `pianokt-upload:${params.sessionId ?? params.songId}:${performanceHash}:${referenceHash}`
    const attemptId = sessionStorage.getItem(key) ?? crypto.randomUUID()
    sessionStorage.setItem(key, attemptId)
    const urls = await backendRequest<{ performance_url: string; reference_url: string }>('/attempts', {
      attempt_id: attemptId, song_id: params.songId, song_source: params.songSource, song_title: params.songTitle,
      duration_sec: params.durationSec, performance_hash: performanceHash, reference_hash: referenceHash,
      session_id: params.sessionId ?? null,
      settings: { ...params.practiceSettings, client_accuracy_pct: params.accuracyPct, client_difficulty: params.difficulty, midi_keyboard_used: params.midiKeyboardUsed },
    })
    for (const [url, bytes] of [[urls.performance_url, performance], [urls.reference_url, reference]] as const) {
      const response = await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'audio/midi', 'x-goog-if-generation-match': '0' }, body: new Uint8Array(bytes) })
      if (!response.ok && response.status !== 412) throw new Error(`MIDI upload failed: ${response.status}`)
    }
    const result = await backendRequest<{ id: string }>(`/attempts/${attemptId}/finalize`, {})
    sessionStorage.removeItem(key)
    return result
  } catch (error) { return { error: error instanceof Error ? error.message : 'Upload failed' } }
}
