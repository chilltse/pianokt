/** One IndexedDB record is the atomic recovery unit: owner + sealed upload bundle. */
import { createStore, del, entries, update } from 'idb-keyval'

export type PendingUpload<T> = { id: string; ownerId: string; payload: T }
export interface UploadStore<T> {
  seal(record: PendingUpload<T>): Promise<void>
  list(): Promise<Array<[string, PendingUpload<T>]>>
  remove(key: string): Promise<void>
}

export function indexedUploadStore<T>(): UploadStore<T> {
  const store = createStore('pianokt-upload-recovery-v2', 'bundles')
  return {
    seal(record) {
      return update(
        `${record.ownerId}:${record.id}`,
        (existing: PendingUpload<T> | undefined) => {
          if (existing && JSON.stringify(existing) !== JSON.stringify(record))
            throw new Error('Immutable upload bundle conflict')
          return existing ?? record
        },
        store,
      )
    },
    list: () => entries<string, PendingUpload<T>>(store),
    remove: (key) => del(key, store),
  }
}

/** A failed request keeps the sealed bundle. Removal happens only after server commit. */
export class UploadQueue<T> {
  private inFlight = new Map<string, Promise<void>>()
  constructor(
    private store: UploadStore<T>,
    private send: (record: PendingUpload<T>) => Promise<void | boolean>,
  ) {}

  async seal(record: PendingUpload<T>, onSealed?: () => void) {
    await this.store.seal(record)
    onSealed?.()
  }

  private deliver(record: PendingUpload<T>) {
    const key = `${record.ownerId}:${record.id}`
    const existing = this.inFlight.get(key)
    if (existing) return existing
    const pending = (async () => {
      const verified = await this.send(record)
      if (verified !== false) await this.store.remove(key)
    })().finally(() => this.inFlight.delete(key))
    this.inFlight.set(key, pending)
    return pending
  }

  async enqueue(record: PendingUpload<T>, onSealed?: () => void) {
    await this.seal(record, onSealed)
    await this.deliver(record)
  }

  async replay(ownerId: string) {
    const failures: unknown[] = []
    for (const [, record] of await this.store.list()) {
      if (record.ownerId !== ownerId) continue
      try {
        await this.deliver(record)
      } catch (error) {
        failures.push(error)
      }
    }
    return failures
  }
}
