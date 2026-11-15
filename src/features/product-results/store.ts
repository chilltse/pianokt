import { backendRequest } from '@/features/challenge-history/gcs'
import { createStore, entries, update } from 'idb-keyval'
import { useEffect, useState } from 'react'
import type { ProductResult } from './model'

const scope = `${import.meta.env.MODE}:${import.meta.env.VITE_PIANOKT_API_URL ?? 'unconfigured'}`
const store = createStore('pianokt-product-results-v1', 'results')
type Record = { owner: string; scope: string; result: ProductResult; synced: boolean }
const notify = () => window.dispatchEvent(new Event('pianokt-product-results'))
export async function persistProductResult(owner: string, result: ProductResult) {
  const key = `${scope}:${owner}:${result.attempt_id}`
  await update(
    key,
    (old: Record | undefined) => {
      if (old && JSON.stringify(old.result) !== JSON.stringify(result))
        throw new Error('Conflicting product result')
      return old ?? { owner, scope, result, synced: false }
    },
    store,
  )
  notify()
  void syncProductResults(owner)
}
export async function syncProductResults(owner: string) {
  for (const [key, row] of await entries<string, Record>(store)) {
    if (row.owner !== owner || row.scope !== scope || row.synced) continue
    try {
      await backendRequest('/product-results', row.result, owner)
      await update(key, (value: Record | undefined) => ({ ...(value ?? row), synced: true }), store)
    } catch {
      /* Durable retry; research delivery has an independent lifecycle. */
    }
  }
}
export function useProductResults(owner?: string) {
  const [state, setState] = useState<{ owner?: string; rows: ProductResult[]; error?: string }>({
    rows: [],
  })
  useEffect(() => {
    if (!owner) return
    let active = true
    let remote: ProductResult[] = []
    const local = async () => {
      const rows = (await entries<string, Record>(store))
        .map(([, r]) => r)
        .filter((r) => r.owner === owner && r.scope === scope)
        .map((r) => r.result)
      if (active)
        setState({
          owner,
          rows: [...new Map([...remote, ...rows].map((r) => [r.attempt_id, r])).values()],
        })
    }
    const refresh = async () => {
      await local()
      await syncProductResults(owner)
      try {
        const collected: ProductResult[] = []
        let cursor: string | null = null
        do {
          const page: { items: ProductResult[]; next: string | null } = await backendRequest(
            `/product-results${cursor ? `?after=${cursor}` : ''}`,
            undefined,
            owner,
          )
          collected.push(...page.items)
          cursor = page.next
        } while (cursor && active)
        remote = collected
        await local()
      } catch {
        if (active)
          setState((s) => ({
            ...s,
            error: 'Showing saved results. Online history is unavailable.',
          }))
      }
    }
    void refresh()
    const timer = setInterval(() => void refresh(), 30000)
    const changed = () => void local()
    const online = () => void refresh()
    window.addEventListener('pianokt-product-results', changed)
    window.addEventListener('online', online)
    return () => {
      active = false
      clearInterval(timer)
      window.removeEventListener('pianokt-product-results', changed)
      window.removeEventListener('online', online)
    }
  }, [owner])
  return state.owner === owner ? state : { rows: [] }
}
