// npm install --prefix /tmp/pianokt-sql-check @electric-sql/pglite
// PGLITE_MODULE=/tmp/pianokt-sql-check/node_modules/@electric-sql/pglite/dist/index.js node backend/tests/schema.mjs
import { readFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
const { PGlite } = await import(process.env.PGLITE_MODULE || '@electric-sql/pglite')
const db = new PGlite()
await db.exec(`create role anon; create role authenticated;
create schema auth;
create table auth.users(id uuid primary key, created_at timestamptz default now());
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;`)
// Use the actual existing event migration, not a mock outbox implementation.
await db.exec(await readFile('supabase/migrations/006_play_events_pipeline.sql','utf8'))
await db.exec(await readFile('supabase/migrations/009_lakehouse_backend.sql','utf8'))
const a='00000000-0000-0000-0000-000000000001', b='00000000-0000-0000-0000-000000000002'
await db.exec(`insert into auth.users(id) values('${a}'),('${b}');`)
await db.exec(`begin; insert into public.play_events_raw(event_id,session_id,user_id,song_id,play_mode,event_type) values(gen_random_uuid(),gen_random_uuid(),'${a}','scale','challenge','play_started'); rollback;`)
assert.equal((await db.query('select count(*)::int n from public.piano_outbox')).rows[0].n,0)
await db.exec(`insert into public.play_events_raw(event_id,session_id,user_id,song_id,play_mode,event_type) values(gen_random_uuid(),gen_random_uuid(),'${a}','scale','challenge','play_started');`)
const outbox=(await db.query('select payload from public.piano_outbox')).rows[0].payload
assert.equal(outbox.event_type,'practice.event'); assert.equal(outbox.user_id,undefined); assert.ok(outbox.learner_key)
await db.exec(`insert into public.piano_attempts(id,user_id,learner_key,song_id,song_source,performance_key,reference_key,performance_hash,reference_hash) values(gen_random_uuid(),'${a}',gen_random_uuid(),'scale','builtin','p','r','h','h'),(gen_random_uuid(),'${b}',gen_random_uuid(),'scale','builtin','p','r','h','h');
set role authenticated; select set_config('request.jwt.claim.sub','${a}',false);`)
assert.equal((await db.query('select count(*)::int n from public.piano_attempts')).rows[0].n,1)
let denied=false
try { await db.query('select * from public.piano_outbox') } catch { denied=true }
assert.equal(denied,true)
await db.close()
console.log('PASS: migration 009, transactional outbox rollback, pseudonymization and RLS isolation')
