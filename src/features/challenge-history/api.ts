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
 * 能够终止一次播放 session 的事件。
 *
 * 普通事件：
 * - play_started
 * - paused
 * - resumed
 *
 * 普通事件只写入 play_events_raw，不立即生成最终汇总。
 *
 * 终止事件：
 * - finished：歌曲自然播放完成
 * - exited：用户主动退出
 * - failed：挑战被判定失败
 *
 * 终止事件成功写入 play_events_raw 后，
 * 会继续调用 upsert_user_play_log，生成或更新 user_play_logs。
 */
const TERMINAL_PLAY_EVENT_TYPES = new Set<PlayEventType>([
  'finished',
  'exited',
  'failed',
])

/**
 * 上传 MIDI 文件，并写入一条 challenge_recordings 记录。
 *
 * 要求：
 * - 用户必须已经登录。
 * - 用户身份只由服务端 auth.uid() 决定。
 * - 不接受客户端传入的 userId，防止伪造其他用户身份。
 */
export async function saveChallengeRecording(params: {
  songSource: string
  songId: string
  songTitle: string | null
  durationSec: number
  midiBase64: string
  accuracyPct?: number
  difficulty?: number

  /**
   * 是否使用了 MIDI 键盘或其他 MIDI 输入设备。
   */
  midiKeyboardUsed?: boolean
}): Promise<{ id: string } | { error: string }> {
  if (!supabase) {
    console.error('[saveChallengeRecording] Supabase not configured')
    return { error: 'Supabase not configured' }
  }

  /**
   * 从 Supabase Auth 获取当前用户。
   *
   * 即使前端已经知道用户信息，这里仍然重新获取，
   * 因为保存录音要求用户处于有效登录状态。
   */
  const {
    data: { user },
  } = await supabase.auth.getUser()

  if (!user) {
    console.error('[saveChallengeRecording] Not authenticated')
    return { error: 'Not authenticated' }
  }

  const {
    songSource,
    songId,
    songTitle,
    durationSec,
    midiBase64,
    accuracyPct,
    difficulty,
    midiKeyboardUsed,
  } = params

  /**
   * 每一条 challenge recording 使用独立 UUID。
   */
  const recordingId = crypto.randomUUID()

  /**
   * Storage 路径以用户 ID 分目录：
   *
   * userId/recordingId.mid
   */
  const path = `${user.id}/${recordingId}.mid`

  /**
   * 把 Base64 MIDI 转换成 Uint8Array，
   * 供 Supabase Storage 上传。
   */
  const binary = Uint8Array.from(
    atob(midiBase64),
    (character) => character.charCodeAt(0),
  )

  /**
   * 第一步：把 MIDI 文件上传到 Storage。
   */
  const { error: uploadError } = await supabase.storage
    .from(BUCKET)
    .upload(path, binary, {
      contentType: 'audio/midi',

      /**
       * 不允许覆盖已有文件。
       *
       * recordingId 是随机 UUID，正常情况下不会重复。
       */
      upsert: false,
    })

  if (uploadError) {
    console.error(
      '[saveChallengeRecording] Storage upload failed:',
      uploadError,
    )

    return {
      error: `Storage: ${uploadError.message}`,
    }
  }

  /**
   * 第二步：调用数据库 RPC，保存 challenge_recordings 记录。
   *
   * Storage 文件已经上传成功，
   * 数据库中保存文件路径和挑战结果。
   */
  const { error: rpcError } = await supabase.rpc(
    'save_challenge_recording',
    {
      recording_id: recordingId,
      p_song_source: songSource,
      p_song_id: songId,
      p_song_title: songTitle,
      p_duration_sec: durationSec,
      p_midi_storage_path: path,
      p_accuracy_pct: accuracyPct ?? 0,
      p_difficulty: difficulty ?? 0,
      p_midi_keyboard_used: midiKeyboardUsed ?? false,
    },
  )

  if (rpcError) {
    console.error(
      '[saveChallengeRecording] RPC save_challenge_recording failed:',
      rpcError,
    )

    return {
      error: `DB: ${rpcError.message}`,
    }
  }

  return {
    id: recordingId,
  }
}

export type LeaderboardSortBy =
  | 'challenges'
  | 'accuracy'
  | 'difficulty'

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
): Promise<
  | { data: LeaderboardEntry[] }
  | { error: string }
> {
  if (!supabase) {
    return {
      error: 'Supabase not configured',
    }
  }

  const { data, error } = await supabase.rpc(
    'get_leaderboard',
    {
      sort_by: sortBy,
    },
  )

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
  | { data: ChallengeRecordingRow[] }
  | { error: string }
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
 * 记录一个播放器事件。
 *
 * 所有事件都会首先写入 play_events_raw。
 *
 * 普通事件：
 * - play_started
 * - paused
 * - resumed
 *
 * 普通事件保存成功后，函数直接返回。
 * 它们不会立即更新 user_play_logs。
 *
 * 终止事件：
 * - finished
 * - exited
 * - failed
 *
 * 终止事件保存成功后，会继续等待：
 *
 * upsert_user_play_log(sessionId)
 *
 * 将该 session 的所有原始事件聚合成一条 user_play_logs。
 *
 * 重要：
 * - 每个事件都会生成一个新的 event_id。
 * - 同一次演奏的所有事件必须使用同一个 sessionId。
 * - finished/exited/failed 写入成功后，才会开始聚合。
 */
export async function logPlayEvent(params: {
  /**
   * 一次完整演奏 session 的唯一 ID。
   *
   * 同一次演奏中的：
   * play_started、paused、resumed、finished
   * 必须使用相同的 sessionId。
   */
  sessionId: string

  /**
   * 当前歌曲 ID。
   */
  songId: string

  /**
   * 当前练习或 exercise ID。
   *
   * 没有 exercise 时可以传 null 或不传。
   */
  exerciseId?: string | null

  /**
   * 播放模式：
   * challenge / freeplay / training
   */
  playMode: PlayMode

  /**
   * 当前事件类型：
   * play_started / paused / resumed /
   * finished / exited / failed
   */
  eventType: PlayEventType

  /**
   * 当前播放头在歌曲中的位置，单位为秒。
   *
   * 例如：
   * - 开始时：0
   * - 播放到 30 秒暂停：30
   * - 最终播放结束：歌曲接近总时长的位置
   *
   * 注意：
   * songTimeSec 不是实际活跃演奏时长。
   * 它只是当前播放位置。
   */
  songTimeSec?: number | null

  /**
   * 事件在客户端发生的时间。
   *
   * 不传时使用当前时间。
   */
  clientTs?: string

  /**
   * 事件附加数据。
   *
   * 对 finished/exited/failed，通常应该包含：
   *
   * {
   *   song_duration_sec: number,
   *   time_playing_sec: number,
   *   success: boolean
   * }
   *
   * 注意数值必须是 number，不能是字符串。
   */
  metadata?: Record<string, unknown>
}): Promise<
  | { ok: true }
  | { error: string }
> {
  if (!supabase) {
    return {
      error: 'Supabase not configured',
    }
  }

  /**
   * 确认当前用户已经登录。
   *
   * 实际 user_id 仍然由数据库函数内部的 auth.uid() 决定，
   * 客户端不会把 userId 传给数据库。
   */
  const {
    data: { user },
  } = await supabase.auth.getUser()

  if (!user) {
    return {
      error: 'Not authenticated',
    }
  }

  /**
   * 第一步：将当前事件写入 play_events_raw。
   *
   * 每一次调用都会生成新的 event_id。
   *
   * 同一次 session：
   * - sessionId 保持不变；
   * - eventId 每个事件都不同。
   *
   * 例如：
   *
   * play_started:
   *   sessionId = A
   *   eventId = E1
   *
   * paused:
   *   sessionId = A
   *   eventId = E2
   *
   * resumed:
   *   sessionId = A
   *   eventId = E3
   *
   * finished:
   *   sessionId = A
   *   eventId = E4
   */
  const { error: eventError } = await supabase.rpc(
    'log_play_event',
    {
      p_event_id: crypto.randomUUID(),
      p_session_id: params.sessionId,
      p_song_id: params.songId,
      p_exercise_id: params.exerciseId ?? null,
      p_play_mode: params.playMode,
      p_event_type: params.eventType,
      p_song_time_sec: params.songTimeSec ?? null,
      p_client_ts:
        params.clientTs ?? new Date().toISOString(),
      p_metadata: params.metadata ?? {},
    },
  )

  /**
   * 原始事件写入失败时：
   * - 不进行聚合；
   * - 直接返回错误。
   */
  if (eventError) {
    console.error(
      '[logPlayEvent] Failed to save raw play event:',
      eventError,
    )

    return {
      error: eventError.message,
    }
  }

  /**
   * 判断当前事件是否为终止事件。
   *
   * paused 和 resumed 不会进入这里。
   *
   * 只有：
   * - finished
   * - exited
   * - failed
   *
   * 才会触发最终聚合。
   */
  const isTerminalEvent =
    TERMINAL_PLAY_EVENT_TYPES.has(params.eventType)

  /**
   * 普通事件只写入原始表。
   *
   * 例如 paused：
   *
   * 1. paused 写入 play_events_raw
   * 2. 不调用 upsert_user_play_log
   * 3. 返回成功
   *
   * 当后面 finished 到达时，
   * 聚合函数会一次性读取之前保存的 pause/resume 等事件。
   */
  if (!isTerminalEvent) {
    return {
      ok: true,
    }
  }

  /**
   * 第二步：只有终止事件才进行最终聚合。
   *
   * 因为上面的 log_play_event 使用了 await，
   * 所以执行到这里时，finished/exited/failed
   * 已经成功写入数据库。
   *
   * 此时再调用 upsert_user_play_log，
   * 聚合函数就能够读取到终止事件。
   */
  const finalizeResult = await finalizeUserPlayLog(
    params.sessionId,
  )

  /**
   * 注意：
   *
   * 这里仍然是两个独立的 RPC：
   *
   * 1. log_play_event
   * 2. upsert_user_play_log
   *
   * 顺序通过 await 保证。
   *
   * 但它们不是同一个 PostgreSQL 原子事务。
   *
   * 因此可能出现：
   * - 原始终止事件已经成功保存；
   * - 聚合 RPC 因临时网络错误而失败。
   *
   * 这种情况下原始数据不会丢失，
   * 之后仍然可以重新调用 finalizeUserPlayLog 修复汇总。
   */
  if ('error' in finalizeResult) {
    console.error(
      '[logPlayEvent] Raw terminal event was saved, '
        + 'but user play log aggregation failed:',
      finalizeResult.error,
    )

    return {
      error:
        'Play event saved, but aggregation failed: '
        + finalizeResult.error,
    }
  }

  /**
   * 终止事件和聚合均已完成。
   */
  return {
    ok: true,
  }
}

/**
 * 聚合一个 session 的全部原始事件，
 * 生成或更新 user_play_logs。
 *
 * 数据库函数会读取：
 * - play_started
 * - paused
 * - resumed
 * - finished
 * - exited
 * - failed
 *
 * 然后计算：
 * - started_at
 * - ended_at
 * - events_count
 * - time_playing
 * - is_played_in_full
 * - exit_status
 *
 * 正常情况下，该函数由 logPlayEvent 在终止事件后自动调用。
 *
 * 仍然将它导出，是为了：
 * - 手动重试；
 * - 修复历史 session；
 * - 聚合失败后的补偿处理。
 */
export async function finalizeUserPlayLog(
  sessionId: string,
): Promise<
  | { ok: true }
  | { error: string }
> {
  if (!supabase) {
    return {
      error: 'Supabase not configured',
    }
  }

  const { error } = await supabase.rpc(
    'upsert_user_play_log',
    {
      p_session_id: sessionId,
    },
  )

  if (error) {
    return {
      error: error.message,
    }
  }

  return {
    ok: true,
  }
}

/**
 * 获取当前登录用户的演奏汇总记录。
 *
 * 每一条 UserPlayLogRow 对应一个 session。
 *
 * 按记录创建时间倒序排列。
 */
export async function listUserPlayLogs(): Promise<
  | { data: UserPlayLogRow[] }
  | { error: string }
> {
  if (!supabase) {
    return {
      error: 'Supabase not configured',
    }
  }

  const { data, error } = await supabase
    .from('user_play_logs')
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
export function isChallengeSuccess(
  accuracyPct: number,
): boolean {
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
export function getBestAccuracyPerSong(
  rows: ChallengeRecordingRow[],
): Map<
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
    const key = `${row.song_source}/${row.song_id}`
    const accuracy = Number(row.accuracy_pct ?? 0)
    const existing = map.get(key)

    /**
     * 当前歌曲还没有记录，
     * 或者本次正确率高于历史最佳时，
     * 更新最高正确率。
     */
    if (
      !existing
      || accuracy > existing.bestAccuracy
    ) {
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
  if (!supabase) {
    return null
  }

  const { data } = await supabase.storage
    .from(BUCKET)
    .createSignedUrl(
      storagePath,
      3600,
    )

  return data?.signedUrl ?? null
}