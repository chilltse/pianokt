-- Run this in Supabase: SQL Editor → New query → paste all → Run
-- Fixes 404 "Could not find the function public.save_challenge_recording"

-- 1) Table
create table if not exists public.challenge_recordings (
  id uuid primary key,
  user_id uuid not null,
  song_source text not null,
  song_id text not null,
  song_title text,
  duration_sec numeric not null,
  midi_storage_path text not null,
  created_at timestamptz not null default now(),
  accuracy_pct numeric not null default 0,
  difficulty numeric not null default 0
);

alter table public.challenge_recordings enable row level security;

drop policy if exists "Users can read own challenge_recordings" on public.challenge_recordings;
create policy "Users can read own challenge_recordings"
  on public.challenge_recordings for select using (auth.uid() = user_id);

drop policy if exists "Users can insert own challenge_recordings" on public.challenge_recordings;
create policy "Users can insert own challenge_recordings"
  on public.challenge_recordings for insert with check (auth.uid() = user_id);

-- 2) RPC (exact parameter names the app sends)
create or replace function public.save_challenge_recording(
  recording_id uuid,
  p_song_source text,
  p_song_id text,
  p_song_title text,
  p_duration_sec numeric,
  p_midi_storage_path text,
  p_accuracy_pct numeric default 0,
  p_difficulty numeric default 0
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.challenge_recordings (
    id, user_id, song_source, song_id, song_title,
    duration_sec, midi_storage_path, accuracy_pct, difficulty
  ) values (
    recording_id, auth.uid(), p_song_source, p_song_id, p_song_title,
    p_duration_sec, p_midi_storage_path, coalesce(p_accuracy_pct, 0), coalesce(p_difficulty, 0)
  );
end;
$$;

grant execute on function public.save_challenge_recording(uuid, text, text, text, numeric, text, numeric, numeric) to authenticated;
grant usage on schema public to authenticated;

-- 3) Verify (should return one row)
select 'save_challenge_recording created' as status
from pg_proc p
join pg_namespace n on p.pronamespace = n.oid
where n.nspname = 'public' and p.proname = 'save_challenge_recording';
