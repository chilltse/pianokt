export type ChallengeRecordingRow = {
  id: string
  user_id: string
  song_source: string
  song_id: string
  song_title: string | null
  duration_sec: number
  midi_storage_path: string
  created_at: string
  accuracy_pct?: number
  difficulty?: number
  midi_keyboard_used?: boolean
}

export type LeaderboardEntry = {
  rank: number
  user_id: string
  display_name: string | null
  avatar_url: string | null
  challenge_count: number
  accuracy_avg: number
  max_difficulty: number
}

export type ChallengeRecording = {
  id: string
  songSource: string
  songId: string
  songTitle: string | null
  durationSec: number
  createdAt: string
  /** 用于播放/下载的 URL（signed 或 public）或 base64；由调用方根据 midi_storage_path 解析 */
  midiUrlOrBase64?: string
}

export type PlayMode = 'challenge' | 'freeplay' | 'training'

export type PlayEventType =
  | 'play_started'
  | 'paused'
  | 'resumed'
  | 'finished'
  | 'exited'
  | 'failed'

export type PlayExitStatus = 'succeeded' | 'abandoned' | 'failed' | 'unknown'

export type UserPlayLogRow = {
  session_id: string
  user_id: string
  song_id: string
  exercise_id: string | null
  play_mode: PlayMode
  days_since_signup: number | null
  time_playing: number
  song_time_sec: number | null
  challenge_recording_id: string | null
  is_played_in_full: boolean
  exit_status: PlayExitStatus
  started_at: string
  ended_at: string
  events_count: number
  created_at: string
  updated_at: string
}
