import { supabase } from '@/features/auth/supabase'

export type ProfileRow = {
  id: string
  email: string | null
  display_name: string | null
  avatar_url: string | null
  created_at: string
  updated_at: string
}

/**
 * 获取当前用户的 profile（RLS 仅返回自己的行）
 */
export async function getProfile(): Promise<
  | { data: ProfileRow }
  | { error: string }
> {
  if (!supabase) return { error: 'Supabase not configured' }
  const { data, error } = await supabase
    .from('profiles')
    .select('*')
    .maybeSingle()
  if (error) return { error: error.message }
  if (!data) return { error: 'Profile not found' }
  return { data: data as ProfileRow }
}

/**
 * 更新当前用户的 display_name / avatar_url，并同步到 auth.user_metadata 以便前端立即生效
 */
export async function updateProfile(updates: {
  display_name?: string | null
  avatar_url?: string | null
}): Promise<{ error?: string }> {
  if (!supabase) return { error: 'Supabase not configured' }
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return { error: 'Not authenticated' }

  const { display_name, avatar_url } = updates
  const row: Record<string, unknown> = { updated_at: new Date().toISOString() }
  if (display_name !== undefined) row.display_name = display_name
  if (avatar_url !== undefined) row.avatar_url = avatar_url

  const { error: updateError } = await supabase
    .from('profiles')
    .update(row)
    .eq('id', user.id)
  if (updateError) return { error: updateError.message }

  const { error: metaError } = await supabase.auth.updateUser({
    data: {
      full_name: display_name ?? user.user_metadata?.full_name,
      avatar_url: avatar_url ?? user.user_metadata?.avatar_url,
    },
  })
  if (metaError) return { error: metaError.message }

  return {}
}
