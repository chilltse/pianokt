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
