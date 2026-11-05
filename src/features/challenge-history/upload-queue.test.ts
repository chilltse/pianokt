import assert from 'node:assert/strict'
import { test } from 'node:test'
import { UploadQueue, type PendingUpload, type UploadStore } from './upload-queue'

test('sealed bundle survives each network failure and replays only for its owner', async () => {
  const rows = new Map<string, PendingUpload<string>>()
  const store: UploadStore<string> = {
    async seal(row) {
      const key = `${row.ownerId}:${row.id}`
      if (rows.has(key) && rows.get(key)?.payload !== row.payload) throw new Error('conflict')
      rows.set(key, structuredClone(row))
    },
    async list() {
      return [...rows]
    },
    async remove(key) {
      rows.delete(key)
    },
  }
  for (const failedStage of ['create', 'put', 'finalize', 'lost-response']) {
    let fail = true
    let calls = 0
    const queue = new UploadQueue(store, async () => {
      calls++
      if (fail) throw new Error(failedStage)
    })
    await assert.rejects(
      queue.enqueue({ id: failedStage, ownerId: 'alice', payload: 'bytes+cycle' }),
    )
    assert.equal(rows.size, 1)
    await queue.replay('bob')
    assert.equal(calls, 1)
    fail = false
    assert.deepEqual(await queue.replay('alice'), [])
    assert.equal(rows.size, 0)
  }
})

test('local persistence failure prevents all network writes', async () => {
  let sent = false
  const queue = new UploadQueue(
    {
      seal: async () => {
        throw new Error('quota')
      },
      list: async () => [],
      remove: async () => {},
    },
    async () => {
      sent = true
    },
  )
  await assert.rejects(queue.enqueue({ id: 'a', ownerId: 'alice', payload: 'raw' }))
  assert.equal(sent, false)
})

test('local seal completes before remote delivery and concurrent recovery sends once', async () => {
  const rows = new Map<string, PendingUpload<string>>()
  let release!: () => void
  const network = new Promise<void>((resolve) => {
    release = resolve
  })
  let sends = 0
  const queue = new UploadQueue<string>(
    {
      async seal(record) {
        rows.set(`${record.ownerId}:${record.id}`, record)
      },
      async list() {
        return [...rows]
      },
      async remove(key) {
        rows.delete(key)
      },
    },
    async () => {
      sends++
      await network
    },
  )
  await queue.seal({ id: 'a', ownerId: 'alice', payload: 'immutable bundle' })
  assert.equal(sends, 0)
  assert.equal(rows.size, 1)
  const first = queue.replay('alice')
  const second = queue.replay('alice')
  await Promise.resolve()
  assert.equal(sends, 1)
  assert.equal(rows.size, 1)
  release()
  assert.deepEqual(await first, [])
  assert.deepEqual(await second, [])
  assert.equal(rows.size, 0)
})

test('accepted receipt retains recovery bytes until server confirms validation', async () => {
  const rows = new Map<string, PendingUpload<string>>()
  let validated = false
  const queue = new UploadQueue<string>(
    {
      async seal(record) {
        rows.set(`${record.ownerId}:${record.id}`, record)
      },
      async list() {
        return [...rows]
      },
      async remove(key) {
        rows.delete(key)
      },
    },
    async () => validated,
  )
  await queue.enqueue({ id: 'a', ownerId: 'alice', payload: 'immutable bundle' })
  assert.equal(rows.size, 1)
  await queue.replay('alice')
  assert.equal(rows.size, 1)
  validated = true
  await queue.replay('alice')
  assert.equal(rows.size, 0)
})
