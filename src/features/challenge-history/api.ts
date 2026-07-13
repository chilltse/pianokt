import { supabase } from '@/features/auth/supabase'
import type {
  ChallengeRecordingRow,
  LeaderboardEntry,
  PlayEventType,
  PlayMode,
  UserPlayLogRow,
} from '@/features/challenge-history/types'

const BUCKET = 'challenge-recordings'

/**
 * 上传 MIDI 并写入一条 challenge_recordings 记录；需已登录。
 * 用户身份仅由服务端 session（auth.uid()）决定，不信任客户端传入的 userId。
 */
export async function saveChallengeRecording(params: {
  songSource: string
  songId: string
  songTitle: string | null
  durationSec: number
  midiBase64: string
  accuracyPct?: number
  difficulty?: number
  /** true if the recording was made with a MIDI keyboard/device connected */
  midiKeyboardUsed?: boolean
}): Promise<{ id: string } | { error: string }> {
  if (!supabase) {
    console.error('[saveChallengeRecording] Supabase not configured')
    return { error: 'Supabase not configured' }
  }

  const { data: { user } } = await supabase.auth.getUser()
  if (!user) {
    console.error('[saveChallengeRecording] Not authenticated')
    return { error: 'Not authenticated' }
  }

  const { songSource, songId, songTitle, durationSec, midiBase64, accuracyPct, difficulty, midiKeyboardUsed } = params

  const recordingId = crypto.randomUUID()
  const path = `${user.id}/${recordingId}.mid`

  const binary = Uint8Array.from(atob(midiBase64), (c) => c.charCodeAt(0))
  const { error: uploadError } = await supabase.storage.from(BUCKET).upload(path, binary, {
    contentType: 'audio/midi',
    upsert: false,
  })
  if (uploadError) {
    console.error('[saveChallengeRecording] Storage upload failed:', uploadError)
    return { error: `Storage: ${uploadError.message}` }
  }

  const { error: rpcError } = await supabase.rpc('save_challenge_recording', {
    recording_id: recordingId,
    p_song_source: songSource,
    p_song_id: songId,
    p_song_title: songTitle,
    p_duration_sec: durationSec,
    p_midi_storage_path: path,
    p_accuracy_pct: accuracyPct ?? 0,
    p_difficulty: difficulty ?? 0,
    p_midi_keyboard_used: midiKeyboardUsed ?? false,
  })
  if (rpcError) {
    console.error('[saveChallengeRecording] RPC save_challenge_recording failed:', rpcError)
    return { error: `DB: ${rpcError.message}` }
  }

  return { id: recordingId }
}

export type LeaderboardSortBy = 'challenges' | 'accuracy' | 'difficulty'

/**
 * 获取排行榜；无需登录。sort_by: challenges | accuracy | difficulty
 */
export async function fetchLeaderboard(sortBy: LeaderboardSortBy = 'challenges'): Promise<
  | { data: LeaderboardEntry[] }
  | { error: string }
> {
  if (!supabase) return { error: 'Supabase not configured' }

  const { data, error } = await supabase.rpc('get_leaderboard', { sort_by: sortBy })

  if (error) return { error: error.message }
  const list = (data ?? []) as LeaderboardEntry[]
  return { data: list }
}

/**
 * 获取当前登录用户的 challenge 录音列表，按创建时间倒序。
 * 不传 userId，完全由 RLS（auth.uid()）隔离，避免客户端篡改。
 */
export async function listChallengeRecordings(): Promise<
  | { data: ChallengeRecordingRow[] }
  | { error: string }
> {
  if (!supabase) return { error: 'Supabase not configured' }

  const { data, error } = await supabase
    .from('challenge_recordings')
    .select('*')
    .order('created_at', { ascending: false })

  if (error) return { error: error.message }
  return { data: (data ?? []) as ChallengeRecordingRow[] }
}

export async function logPlayEvent(params: {
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

  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return { error: 'Not authenticated' }

  const { error } = await supabase.rpc('log_play_event', {
    p_event_id: crypto.randomUUID(),
    p_session_id: params.sessionId,
    p_song_id: params.songId,
    p_exercise_id: params.exerciseId ?? null,
    p_play_mode: params.playMode,
    p_event_type: params.eventType,
    p_song_time_sec: params.songTimeSec ?? null,
    p_client_ts: params.clientTs ?? new Date().toISOString(),
    p_metadata: params.metadata ?? {},
  })
  if (error) return { error: error.message }

  return { ok: true }
}

export async function finalizeUserPlayLog(sessionId: string): Promise<{ ok: true } | { error: string }> {
  if (!supabase) return { error: 'Supabase not configured' }
  const { error } = await supabase.rpc('upsert_user_play_log', { p_session_id: sessionId })
  if (error) return { error: error.message }
  return { ok: true }
}

export async function listUserPlayLogs(): Promise<{ data: UserPlayLogRow[] } | { error: string }> {
  if (!supabase) return { error: 'Supabase not configured' }
  const { data, error } = await supabase
    .from('user_play_logs')
    .select('*')
    .order('created_at', { ascending: false })

  if (error) return { error: error.message }
  return { data: (data ?? []) as UserPlayLogRow[] }
}

const CHALLENGE_SUCCESS_PCT = 90

/** 正确率 ≥ 90% 视为挑战成功（perfect+good 占所有 notes 的比例） */
export function isChallengeSuccess(accuracyPct: number): boolean {
  return accuracyPct >= CHALLENGE_SUCCESS_PCT
}

/**
 * 按歌曲聚合录音，得到每首歌的最高正确率。key = `${song_source}/${song_id}`
 */
export function getBestAccuracyPerSong(
  rows: ChallengeRecordingRow[],
): Map<string, { bestAccuracy: number; songTitle: string | null }> {
  const map = new Map<string, { bestAccuracy: number; songTitle: string | null }>()
  for (const row of rows) {
    const key = `${row.song_source}/${row.song_id}`
    const acc = Number(row.accuracy_pct ?? 0)
    const existing = map.get(key)
    if (!existing || acc > existing.bestAccuracy) {
      map.set(key, { bestAccuracy: acc, songTitle: row.song_title })
    }
  }
  return map
}

/**
 * 获取单条录音的下载 URL（signed，有效期 1 小时）。
 */
export async function getChallengeRecordingDownloadUrl(storagePath: string): Promise<
  string | null
> {
  if (!supabase) return null
  const { data } = await supabase.storage.from(BUCKET).createSignedUrl(storagePath, 3600)
  return data?.signedUrl ?? null
}
