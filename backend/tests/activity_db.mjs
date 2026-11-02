// JSON-lines PostgreSQL adapter for Python integration tests. Uses an isolated in-memory database.
import { readFile } from 'node:fs/promises'
import { createInterface } from 'node:readline'

const { PGlite } = await import(process.env.PGLITE_MODULE)
const db = new PGlite()
await db.exec(`create role anon; create role authenticated; create role pianokt_backend;
create schema auth; create table auth.users(id uuid primary key,created_at timestamptz default now());
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
create table public.challenge_recordings(id uuid primary key,user_id uuid,song_id text,created_at timestamptz default now());`)
for (const name of [
  '006_play_events_pipeline.sql',
  '007_user_play_logs_song_time_and_recording_link.sql',
  '009_lakehouse_backend.sql',
  '013_visits_and_attempt_activity.sql',
]) {
  await db.exec(await readFile(`supabase/migrations/${name}`, 'utf8'))
}
console.log(JSON.stringify({ ready: true }))
for await (const line of createInterface({ input: process.stdin })) {
  try {
    const { sql, params } = JSON.parse(line)
    const result = await db.query(sql, params)
    console.log(JSON.stringify({ rows: result.rows }))
  } catch (error) {
    console.log(JSON.stringify({ error: String(error) }))
  }
}
await db.close()
