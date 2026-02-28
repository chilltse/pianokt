import { supabase } from '@/features/auth/supabase'

const AVATAR_BUCKET = 'avatars'
const MAX_SIZE_BYTES = 2 * 1024 * 1024 // 2MB
const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']

function getExtension(type: string): string {
  const map: Record<string, string> = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
  }
  return map[type] ?? 'jpg'
}

/**
 * 校验头像文件：类型与大小
 */
export function validateAvatarFile(file: File): { ok: true } | { ok: false; error: string } {
  if (!ALLOWED_TYPES.includes(file.type)) {
    return {
      ok: false,
      error: 'Please use a JPEG, PNG, WebP or GIF image.',
    }
  }
  if (file.size > MAX_SIZE_BYTES) {
    return {
      ok: false,
      error: `File must be under ${MAX_SIZE_BYTES / 1024 / 1024}MB.`,
    }
  }
  return { ok: true }
}

/**
 * 上传头像到 Storage（avatars/{user_id}/avatar.{ext}），返回公开 URL。
 * 需已登录；仅允许上传到自己的路径（RLS/策略保证）。
 */
export async function uploadAvatar(file: File): Promise<{ url: string } | { error: string }> {
  if (!supabase) return { error: 'Supabase not configured' }
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) return { error: 'Not authenticated' }

  const validation = validateAvatarFile(file)
  if (!validation.ok) return { error: validation.error }

  const ext = getExtension(file.type)
  const path = `${user.id}/avatar.${ext}`

  const { error: uploadError } = await supabase.storage.from(AVATAR_BUCKET).upload(path, file, {
    contentType: file.type,
    upsert: true,
  })
  if (uploadError) return { error: uploadError.message }

  const {
    data: { publicUrl },
  } = supabase.storage.from(AVATAR_BUCKET).getPublicUrl(path)
  return { url: publicUrl }
}
