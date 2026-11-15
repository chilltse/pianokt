import { useOptionalAuth } from '@/features/auth'
import { supabase } from '@/features/auth/supabase'
import { useEffect, useRef, useState } from 'react'
import useSWR from 'swr'

// Serialize mutations per user: overlapping SWR optimistic transactions on the
// same list could otherwise roll back a different segment's successful write.
const pending = new Set<string>()
export function useSegmentMarks() {
  const userId = useOptionalAuth()?.user?.id
  const owner = useRef(userId)
  owner.current = userId
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  useEffect(() => {
    setError('')
    setBusy(null)
  }, [userId])
  const result = useSWR<string[]>(
    userId ? ['segment-marks', userId] : null,
    async () => {
      if (!supabase) throw new Error('Supabase is not configured')
      const { data, error } = await supabase
        .from('piano_segment_marks')
        .select('segment_id')
        .eq('user_id', userId!)
      if (error) throw error
      return data.map((row) => row.segment_id as string)
    },
    { keepPreviousData: false, revalidateOnMount: true },
  )
  async function toggle(segmentId: string) {
    if (!userId || !supabase) {
      setError('Please sign in to mark segments.')
      return
    }
    if (!result.data || result.error) {
      setError('Please reload your marks before making changes.')
      return
    }
    if (pending.has(userId)) return
    pending.add(userId)
    setBusy(segmentId)
    setError('')
    const marked = result.data.includes(segmentId)
    const update = (current: string[] = []) =>
      marked ? current.filter((id) => id !== segmentId) : [...new Set([...current, segmentId])]
    const client = supabase
    try {
      await result.mutate(
        async (current) => {
          const query = marked
            ? client
                .from('piano_segment_marks')
                .delete()
                .eq('user_id', userId)
                .eq('segment_id', segmentId)
            : client
                .from('piano_segment_marks')
                .upsert(
                  { user_id: userId, segment_id: segmentId },
                  { onConflict: 'user_id,segment_id', ignoreDuplicates: true },
                )
          const { error } = await query
          if (error) throw error
          return update(current)
        },
        {
          optimisticData: update,
          rollbackOnError: true,
          populateCache: true,
          revalidate: false,
          throwOnError: true,
        },
      )
    } catch (e) {
      if (owner.current === userId)
        setError(e instanceof Error ? e.message : 'Unable to save mark. Please retry.')
    } finally {
      pending.delete(userId)
      if (owner.current === userId) setBusy(null)
    }
  }
  return {
    ...result,
    userId,
    marks: new Set(userId ? (result.data ?? []) : []),
    toggle,
    busy,
    message: error,
  }
}
