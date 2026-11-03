/** Legacy freeplay/training compatibility. Challenge uploads use /finalize. */
import { supabase } from '@/features/auth/supabase'
import type { PlayEventType, PlayMode } from './types'

export async function logPlayEvent(params: {
  eventId?: string
  sessionId: string
  songId: string
  exerciseId?: string | null
  playMode: PlayMode
  eventType: PlayEventType
  songTimeSec?: number | null
  clientTs?: string
  metadata?: Record<string, unknown>
}): Promise<{ ok: true } | { error: string }> {
  if (!supabase) return { error: 'Supabase not configured' }
  const { error } = await supabase.rpc('log_play_event_atomic', {
    p_event_id: params.eventId ?? crypto.randomUUID(),
    p_session_id: params.sessionId,
    p_song_id: params.songId,
    p_exercise_id: params.exerciseId ?? null,
    p_play_mode: params.playMode,
    p_event_type: params.eventType,
    p_song_time_sec: params.songTimeSec ?? null,
    p_client_ts: params.clientTs ?? new Date().toISOString(),
    p_metadata: params.metadata ?? {},
  })
  return error ? { error: error.message } : { ok: true }
}

/** Historical repair only; backend-owned summaries are protected by the RPC. */
export async function finalizeUserPlayLog(
  sessionId: string,
): Promise<{ ok: true } | { error: string }> {
  if (!supabase) return { error: 'Supabase not configured' }
  const { error } = await supabase.rpc('upsert_user_play_log', { p_session_id: sessionId })
  return error ? { error: error.message } : { ok: true }
}
