import { supabase } from '@/features/auth/supabase'
import type {
  ChallengeRecordingRow,
  LeaderboardEntry,
  UserPlayLogRow,
} from '@/features/challenge-history/types'
import { backendRequest } from './gcs'

const BUCKET = 'challenge-recordings'

export { saveGcsRecording as saveChallengeRecording } from './gcs'

export type LeaderboardSortBy = 'challenges' | 'accuracy' | 'difficulty'

/**
 * 获取排行榜。
 *
 * 无需登录。
 *
 * sortBy 可选：
 * - challenges：按照挑战次数排序
 * - accuracy：按照正确率排序
 * - difficulty：按照难度排序
 */
export async function fetchLeaderboard(
  sortBy: LeaderboardSortBy = 'challenges',
): Promise<{ data: LeaderboardEntry[] } | { error: string }> {
  if (!supabase) {
    return {
      error: 'Supabase not configured',
    }
  }

  const { data, error } = await supabase.rpc('get_leaderboard', {
    sort_by: sortBy,
  })

  if (error) {
    return {
      error: error.message,
    }
  }

  const list = (data ?? []) as LeaderboardEntry[]

  return {
    data: list,
  }
}

/**
 * 获取当前登录用户的挑战录音列表。
 *
 * 数据按照 created_at 倒序排列。
 *
 * 这里不传 userId。
 * 用户隔离完全依赖 Supabase RLS 和 auth.uid()，
 * 防止客户端尝试读取其他用户的数据。
 */
export async function listChallengeRecordings(): Promise<
  { data: ChallengeRecordingRow[] } | { error: string }
> {
  if (!supabase) {
    return {
      error: 'Supabase not configured',
    }
  }

  const { data, error } = await supabase
    .from('challenge_recordings')
    .select('*')
    .order('created_at', {
      ascending: false,
    })

  if (error) {
    return {
      error: error.message,
    }
  }

  return {
    data: (data ?? []) as ChallengeRecordingRow[],
  }
}

/**
 * 获取当前登录用户的演奏汇总记录。
 *
 * 每一条 UserPlayLogRow 对应一个 session。
 *
 * 按记录创建时间倒序排列。
 */
export async function listUserPlayLogs(): Promise<{ data: UserPlayLogRow[] } | { error: string }> {
  if (!supabase) {
    return {
      error: 'Supabase not configured',
    }
  }

  const { data, error } = await supabase.from('user_play_logs').select('*').order('created_at', {
    ascending: false,
  })

  if (error) {
    return {
      error: error.message,
    }
  }

  return {
    data: (data ?? []) as UserPlayLogRow[],
  }
}

const CHALLENGE_SUCCESS_PCT = 90

/**
 * 判断一次 Challenge 是否成功。
 *
 * 正确率计算通常是：
 *
 * perfect + good
 * ---------------- × 100
 * 所有 notes
 *
 * 正确率大于或等于 90% 时，
 * 认为本次挑战成功。
 */
export function isChallengeSuccess(accuracyPct: number): boolean {
  return accuracyPct >= CHALLENGE_SUCCESS_PCT
}

/**
 * 按歌曲聚合所有 challenge recording，
 * 计算每首歌曲的历史最高正确率。
 *
 * Map key：
 *
 * `${song_source}/${song_id}`
 *
 * Map value：
 *
 * {
 *   bestAccuracy,
 *   songTitle
 * }
 */
export function getBestAccuracyPerSong(rows: ChallengeRecordingRow[]): Map<
  string,
  {
    bestAccuracy: number
    songTitle: string | null
  }
> {
  const map = new Map<
    string,
    {
      bestAccuracy: number
      songTitle: string | null
    }
  >()

  for (const row of rows) {
    if (row.score_source !== 'aligned') continue
    const key = `${row.song_source}/${row.song_id}`
    const accuracy = Number(row.accuracy_pct ?? 0)
    const existing = map.get(key)

    /**
     * 当前歌曲还没有记录，
     * 或者本次正确率高于历史最佳时，
     * 更新最高正确率。
     */
    if (!existing || accuracy > existing.bestAccuracy) {
      map.set(key, {
        bestAccuracy: accuracy,
        songTitle: row.song_title,
      })
    }
  }

  return map
}

/**
 * 获取某条 MIDI 录音的临时下载 URL。
 *
 * URL 为 signed URL，
 * 有效时间为 3600 秒，即 1 小时。
 */
export async function getChallengeRecordingDownloadUrl(
  storagePath: string,
): Promise<string | null> {
  if (storagePath.startsWith('gcs:')) {
    return (await backendRequest<{ url: string }>(`/attempts/${storagePath.slice(4)}/download`)).url
  }
  if (!supabase) {
    return null
  }

  const { data } = await supabase.storage.from(BUCKET).createSignedUrl(storagePath, 3600)

  return data?.signedUrl ?? null
}
