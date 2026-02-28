-- Run this in Supabase Dashboard: SQL Editor → New query → paste → Run (once)
-- Creates: challenge_recordings table, save_challenge_recording RPC, get_leaderboard RPC, storage bucket + policies
--
-- Optional: if you don't have public.profiles yet, uncomment the block below.
-- create table if not exists public.profiles (
--   id uuid primary key references auth.users(id) on delete cascade,
--   email text,
--   display_name text,
--   avatar_url text,
--   created_at timestamptz default now(),
--   updated_at timestamptz default now()
-- );
-- alter table public.profiles enable row level security;
-- create policy "Users can read own profile" on public.profiles for select using (auth.uid() = id);
-- create policy "Users can update own profile" on public.profiles for update using (auth.uid() = id);
-- create or replace function public.handle_new_user() returns trigger as $$ begin insert into public.profiles (id, email, display_name) values (new.id, new.email, new.raw_user_meta_data->>'full_name'); return new; end; $$ language plpgsql security definer;
-- drop trigger if exists on_auth_user_created on auth.users;
-- create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_user();
-- (Then backfill: insert into public.profiles (id, email, display_name) select id, email, raw_user_meta_data->>'full_name' from auth.users on conflict (id) do nothing;)

-- 1) Table for challenge recordings (one row per recording)
create table if not exists public.challenge_recordings (
  id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
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

-- Users can only read/insert their own rows
create policy "Users can read own challenge_recordings"
  on public.challenge_recordings for select
  using (auth.uid() = user_id);

create policy "Users can insert own challenge_recordings"
  on public.challenge_recordings for insert
  with check (auth.uid() = user_id);

-- 2) RPC: save one recording (called after uploading MIDI to storage)
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
    p_duration_sec, p_midi_storage_path, p_accuracy_pct, p_difficulty
  );
end;
$$;

grant execute on function public.save_challenge_recording(uuid, text, text, text, numeric, text, numeric, numeric) to authenticated;

-- 3) RPC: leaderboard (sort_by: 'challenges' | 'accuracy' | 'difficulty')
-- Requires public.profiles with id, display_name, avatar_url (e.g. from Supabase Auth + your profile setup)
create or replace function public.get_leaderboard(sort_by text default 'challenges')
returns table (
  rank bigint,
  user_id uuid,
  display_name text,
  avatar_url text,
  challenge_count bigint,
  accuracy_avg numeric,
  max_difficulty numeric
)
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with agg as (
    select
      cr.user_id,
      count(*)::bigint as challenge_count,
      avg(cr.accuracy_pct) as accuracy_avg,
      coalesce(max(cr.difficulty), 0)::numeric as max_difficulty
    from public.challenge_recordings cr
    group by cr.user_id
  ),
  ordered as (
    select
      a.user_id,
      p.display_name,
      p.avatar_url,
      a.challenge_count,
      a.accuracy_avg,
      a.max_difficulty,
      row_number() over (order by
        case sort_by
          when 'accuracy' then a.accuracy_avg
          when 'difficulty' then a.max_difficulty
          else a.challenge_count
        end desc nulls last
      ) as rn
    from agg a
    left join public.profiles p on p.id = a.user_id
  )
  select o.rn as rank, o.user_id, o.display_name, o.avatar_url, o.challenge_count, o.accuracy_avg, o.max_difficulty
  from ordered o
  order by o.rn;
end;
$$;

grant execute on function public.get_leaderboard(text) to anon;
grant execute on function public.get_leaderboard(text) to authenticated;

-- 4) Storage bucket for MIDI files (create if not exists)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'challenge-recordings',
  'challenge-recordings',
  false,
  null,
  array['audio/midi']
)
on conflict (id) do update set
  allowed_mime_types = coalesce(storage.buckets.allowed_mime_types, excluded.allowed_mime_types);

-- Policy: authenticated users can upload only to their own folder (user_id/filename)
create policy "Users can upload own challenge recordings"
  on storage.objects for insert
  to authenticated
  with check (
    bucket_id = 'challenge-recordings'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- Policy: users can read their own files (for signed URL download)
create policy "Users can read own challenge recordings"
  on storage.objects for select
  to authenticated
  using (
    bucket_id = 'challenge-recordings'
    and (storage.foldername(name))[1] = auth.uid()::text
  );
