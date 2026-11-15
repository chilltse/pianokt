export type ProductResult = {
  attempt_id: string
  song_source: 'builtin' | 'local'
  song_id: string
  segment_id?: string
  segment_index?: number
  midi_sha256?: string
  frontend_note_accuracy: number | null
  completion: number
  is_completed: boolean
  completion_reason: 'finished' | 'exited'
  started_at: string
  ended_at: string
  duration: number
  scoring_version: 'frontend-note-v1'
}
export const productKey = (r: ProductResult) => `${r.song_source}/${r.segment_id ?? r.song_id}`
export function aggregateResults(results: ProductResult[], succeeds: (pct: number) => boolean) {
  const groups = new Map<string, ProductResult[]>()
  for (const r of new Map(results.map((r) => [r.attempt_id, r])).values()) {
    const key = productKey(r)
    groups.set(key, [...(groups.get(key) ?? []), r])
  }
  return [...groups]
    .map(([key, rows]) => {
      rows.sort(
        (a, b) => b.ended_at.localeCompare(a.ended_at) || b.attempt_id.localeCompare(a.attempt_id),
      )
      const full = rows.filter((r) => r.is_completed)
      const accuracies = full.flatMap((r) =>
        r.frontend_note_accuracy === null ? [] : [r.frontend_note_accuracy],
      )
      return {
        key,
        rows,
        latest: rows[0],
        completion: Math.max(...rows.map((r) => r.completion)),
        accuracy: accuracies.length ? Math.max(...accuracies) : null,
        status: full.some(
          (r) => r.frontend_note_accuracy !== null && succeeds(r.frontend_note_accuracy * 100),
        )
          ? 'Succeeded'
          : full.length
            ? 'Completed'
            : 'In Progress',
      }
    })
    .sort((a, b) => b.latest.ended_at.localeCompare(a.latest.ended_at))
}
