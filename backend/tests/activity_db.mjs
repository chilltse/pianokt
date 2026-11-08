// JSON-lines PostgreSQL adapter for Python integration tests. Uses an isolated in-memory database.
import { readFile } from 'node:fs/promises'
import { createInterface } from 'node:readline'

const { PGlite } = await import(process.env.PGLITE_MODULE)
const db = new PGlite()
await db.exec(`create role anon; create role authenticated; create role pianokt_backend; create role service_role;
create schema storage; create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);
create schema auth; create table auth.users(id uuid primary key,created_at timestamptz default now());
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
create table public.profiles(id uuid primary key,display_name text,avatar_url text);
create table public.challenge_recordings(id uuid primary key,user_id uuid,song_id text,song_source text,song_title text,duration_sec numeric,midi_storage_path text,midi_keyboard_used boolean,accuracy_pct numeric default 0,difficulty numeric default 0,created_at timestamptz default now());`)
for (const name of [
  '006_play_events_pipeline.sql',
  '007_user_play_logs_song_time_and_recording_link.sql',
  '009_lakehouse_backend.sql',
  '011_performance_event_telemetry.sql',
  '013_visits_and_attempt_activity.sql',
  '014_atomic_attempt_commit.sql',
  '015_direct_export_commit.sql',
  '016_challenge_event_authority.sql','017_async_finalization.sql','018_song_versions.sql','019_practice_catalog_identity.sql',
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
