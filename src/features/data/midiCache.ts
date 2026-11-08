import { get, set } from 'idb-keyval'

export type MidiIdentity = { midi_sha256: string; storage_path: string }
export type BinaryStore = {
  get(key: string): Promise<ArrayBuffer | undefined>
  set(key: string, data: ArrayBuffer): Promise<unknown>
}
export const binaryStore: BinaryStore = { get, set }
export async function sha256(bytes: ArrayBuffer) {
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

// Callers may cancel their subscription without aborting another caller's shared download.
export function cancellable<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'))
    if (signal.aborted) {
      abort()
      return
    }
    signal.addEventListener('abort', abort, { once: true })
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
  })
}
export function createMidiLoader(
  store: BinaryStore,
  download: (path: string) => Promise<ArrayBuffer>,
) {
  const pending = new Map<string, Promise<ArrayBuffer>>()
  return (song: MidiIdentity, signal?: AbortSignal) => {
    if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'))
    const hash = song.midi_sha256
    if (!/^[a-f0-9]{64}$/.test(hash)) return Promise.reject(new Error('Invalid MIDI SHA-256'))
    let task = pending.get(hash)
    if (!task) {
      task = (async () => {
        const key = `midi:${hash}`
        try {
          const cached = await store.get(key)
          if (cached && (await sha256(cached)) === hash) return cached
        } catch {
          /* Storage can be denied or evicted. Playback still works. */
        }
        const bytes = await download(song.storage_path)
        if ((await sha256(bytes)) !== hash) throw new Error('MIDI SHA-256 mismatch')
        try {
          await store.set(key, bytes)
        } catch {
          /* Quota must not block playback. */
        }
        return bytes
      })().finally(() => pending.delete(hash))
      pending.set(hash, task)
    }
    return cancellable(task, signal)
  }
}
