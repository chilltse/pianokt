import { useState } from 'react'
import { Link } from 'react-router'
import { backendRequest } from './gcs'
type Result = { recommendation_id: string; is_demo: boolean; items: { song_id: string; title: string }[] }
export function Recommendations() {
  const [result, setResult] = useState<Result | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  async function feedback(r: Result, song: string, type: string) {
    try { await backendRequest('/recommendations/feedback', { event_id: crypto.randomUUID(), recommendation_id: r.recommendation_id, song_id: song, event_type: type }) }
    catch { /* Logging failure must not block practice navigation. */ }
  }
  async function load() {
    setBusy(true); setError('')
    try {
      const r = await backendRequest<Result>('/recommendations', {}); setResult(r)
      for (const item of r.items) void feedback(r, item.song_id, 'impression')
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load recommendations') }
    finally { setBusy(false) }
  }
  return <section className="my-6 rounded border border-amber-100 bg-white p-4">
    <button className="underline" disabled={busy} onClick={load}>{busy ? 'Loading…' : 'Find next practice pieces'}</button>
    <p className="mt-2 text-sm text-stone-500">Demo recommendations. A trained ability model is not connected yet.</p>
    {error && <p role="alert">{error}</p>}
    {result && result.items.length === 0 && <p>No pieces configured in the recommendation catalog.</p>}
    {result && <ul>{result.items.map(item => <li key={item.song_id}>
      <Link className="underline" to={`/challenge?source=builtin&id=${encodeURIComponent(item.song_id)}`} onClick={() => void feedback(result,item.song_id,'click')}>{item.title}</Link>
    </li>)}</ul>}
  </section>
}
