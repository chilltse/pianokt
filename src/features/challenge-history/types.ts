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
  sessionId: string
  songSource: string
  songId: string
  songTitle: string | null
  durationSec: number
  createdAt: string
  midiUrlOrBase64?: string
}

/** Challenge session row from user_play_logs (replaces challenge_recordings). */
export type ChallengeRecordingRow = {
  session_id: string
  user_id: string
  song_source: string
  song_id: string
  song_title: string | null
  duration_sec: number | null
  midi_storage_path: string | null
  created_at: string
  accuracy_pct?: number | null
  difficulty?: number | null
  midi_keyboard_used?: boolean | null
  song_time_sec?: number | null
  time_playing?: number | null
  exit_status?: PlayExitStatus | null
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
  is_played_in_full: boolean
  exit_status: PlayExitStatus
  started_at: string
  ended_at: string
  events_count: number
  song_source: string | null
  song_title: string | null
  duration_sec: number | null
  midi_storage_path: string | null
  accuracy_pct: number | null
  difficulty: number | null
  midi_keyboard_used: boolean | null
  created_at: string
  updated_at: string
}
