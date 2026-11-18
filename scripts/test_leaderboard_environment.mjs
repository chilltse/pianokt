import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
const {PGlite}=await import(process.env.PGLITE_MODULE || '@electric-sql/pglite')
const db=new PGlite()
await db.exec(`create role anon; create role authenticated; create schema auth; create table auth.users(id uuid primary key); create function auth.uid() returns uuid language sql as $$select null::uuid$$; create table profiles(id uuid,display_name text,avatar_url text);`)
for(const name of ['022_product_results.sql','025_product_leaderboard_environment.sql']) await db.exec(await readFile('supabase/migrations/'+name,'utf8'))
await db.exec(`insert into auth.users values('00000000-0000-0000-0000-000000000001'); insert into challenge_results(attempt_id,user_id,source_environment,payload,difficulty) values(gen_random_uuid(),'00000000-0000-0000-0000-000000000001','development','{"is_completed":true,"is_succeeded":true,"segment_id":"part"}',2);`)
assert.equal((await db.query("select * from get_product_leaderboard('challenges','development')")).rows[0].challenge_count,1)
assert.equal((await db.query("select * from get_product_leaderboard('challenges','production')")).rows.length,0)
assert.equal((await db.query("select * from get_product_leaderboard('challenges')")).rows.length,0)
assert.equal((await db.query('select * from get_product_leaderboard()')).rows.length,0)
await db.close(); console.log('PASS development isolation and legacy RPC compatibility')
