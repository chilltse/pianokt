begin;
-- Typed content identity survives interrupted attempts and projection rebuilds.
-- No title/filename lookup, no guesses or backfill for old records.
alter table public.student_attempts add column if not exists segment_id uuid,
 add column if not exists segment_index integer check(segment_index>0),
 add column if not exists midi_sha256 text check(midi_sha256 ~ '^[a-f0-9]{64}$');
alter table public.piano_attempts add column if not exists segment_id uuid,
 add column if not exists segment_index integer check(segment_index>0),
 add column if not exists midi_sha256 text check(midi_sha256 ~ '^[a-f0-9]{64}$');
alter table public.challenge_recordings add column if not exists segment_id uuid,
 add column if not exists segment_index integer check(segment_index>0),
 add column if not exists midi_sha256 text check(midi_sha256 ~ '^[a-f0-9]{64}$');
alter table public.user_play_logs add column if not exists segment_id uuid,
 add column if not exists segment_index integer check(segment_index>0),
 add column if not exists midi_sha256 text check(midi_sha256 ~ '^[a-f0-9]{64}$');
create index if not exists challenge_recordings_segment on public.challenge_recordings(user_id,segment_id,created_at desc);
create index if not exists student_attempts_segment on public.student_attempts(segment_id,started_at);
-- One JSON result avoids PostgREST's default row limit truncating large catalogs.
create or replace function public.piano_public_catalog() returns jsonb
language sql stable security invoker set search_path='' as $$
 select coalesce(jsonb_agg(to_jsonb(c) order by c.composer,c.title,c.song_id,c.segment_index),'[]'::jsonb)
 from public.piano_public_song_catalog c
$$;
revoke all on function public.piano_public_catalog() from public;
grant execute on function public.piano_public_catalog() to anon,authenticated;

commit;
