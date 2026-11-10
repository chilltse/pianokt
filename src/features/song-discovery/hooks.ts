import { useOptionalAuth } from '@/features/auth'
import { supabase } from '@/features/auth/supabase'
import type { SongMetadata } from '@/types'
import { useState } from 'react'
import useSWR from 'swr'
import { recommendationSegments } from './model'

const pending = new Set<string>()
export function useSegmentMarks() {
  const userId = useOptionalAuth()?.user?.id
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const result = useSWR(userId ? ['segment-marks', userId] : null, async () => {
    if (!supabase) throw new Error('Supabase is not configured')
    const { data, error } = await supabase
      .from('piano_segment_marks')
      .select('segment_id')
      .eq('user_id', userId!)
    if (error) throw error
    return data.map((row) => row.segment_id as string)
  })
  async function toggle(segmentId: string) {
    if (!userId || !supabase) {
      setError('Please sign in to mark segments.')
      return
    }
    if (!result.data || result.error) {
      setError('Please reload your marks before making changes.')
      return
    }
    const key = `${userId}/${segmentId}`
    if (pending.has(key)) return
    pending.add(key)
    setBusy(segmentId)
    setError('')
    const marked = result.data.includes(segmentId)
    try {
      // Commit the visible state only after persistence succeeds; failures keep the prior state.
      const query = marked
        ? supabase
            .from('piano_segment_marks')
            .delete()
            .eq('user_id', userId)
            .eq('segment_id', segmentId)
        : supabase
            .from('piano_segment_marks')
            .upsert(
              { user_id: userId, segment_id: segmentId },
              { onConflict: 'user_id,segment_id', ignoreDuplicates: true },
            )
      const { error } = await query
      if (error) throw error
      await result.mutate(
        (current) =>
          marked
            ? current?.filter((id) => id !== segmentId)
            : [...new Set([...(current ?? []), segmentId])],
        { revalidate: false },
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to save mark. Please retry.')
    } finally {
      pending.delete(key)
      setBusy(null)
    }
  }
  return { ...result, userId, marks: new Set(result.data ?? []), toggle, busy, message: error }
}

export function useSegmentRecommendations(songs: SongMetadata[]) {
  const userId = useOptionalAuth()?.user?.id
  return useSWR(userId ? ['segment-recommendations', userId, songs] : null, async () => {
    if (!supabase) throw new Error('Supabase is not configured')
    // Scan newest first, skipping demo, malformed and no-longer-playable batches.
    for (let offset = 0; ; offset += 100) {
      const { data, error } = await supabase
        .from('piano_recommendations')
        .select('id,response')
        .eq('user_id', userId!)
        .eq('response->>is_demo', 'false')
        .order('created_at', { ascending: false })
        .order('id', { ascending: false })
        .range(offset, offset + 99)
      if (error) throw error
      for (const row of data) {
        const items = recommendationSegments(row.response, songs)
        if (items.length) return items
      }
      if (data.length < 100) return []
    }
  })
}
