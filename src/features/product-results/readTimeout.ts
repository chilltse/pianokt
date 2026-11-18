/** Bounds authentication, IndexedDB and network reads, not only fetch itself. */
export async function withHistoryTimeout<T>(read: Promise<T>, timeoutMs = 10000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([
      read,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('Challenge history read timed out')), timeoutMs)
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}
