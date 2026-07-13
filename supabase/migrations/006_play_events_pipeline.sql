-- Raw play events + aggregated user play logs pipeline

create table if not exists public.play_events_raw (
  event_id uuid primary key,
  session_id uuid not null,
  user_id uuid not null references auth.users(id) on delete cascade,
  song_id text not null,
  exercise_id text,
  play_mode text not null,
  event_type text not null,
  song_time_sec numeric,
  client_ts timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  check (play_mode in ('challenge', 'freeplay', 'training')),
  check (event_type in ('play_started', 'paused', 'resumed', 'finished', 'exited', 'failed'))
);

create index if not exists play_events_raw_user_created_idx
  on public.play_events_raw (user_id, created_at desc);

create index if not exists play_events_raw_session_created_idx
  on public.play_events_raw (session_id, created_at asc);

alter table public.play_events_raw enable row level security;

create policy "Users can read own play_events_raw"
  on public.play_events_raw for select
  using (auth.uid() = user_id);

create policy "Users can insert own play_events_raw"
  on public.play_events_raw for insert
  with check (auth.uid() = user_id);

create table if not exists public.user_play_logs (
  session_id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  song_id text not null,
  exercise_id text,
  play_mode text not null,
  days_since_signup numeric,
  time_playing numeric not null default 0,
  is_played_in_full boolean not null default false,
  exit_status text not null,
  started_at timestamptz not null,
  ended_at timestamptz not null,
  events_count integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (exit_status in ('succeeded', 'abandoned', 'failed', 'unknown'))
);

create index if not exists user_play_logs_user_created_idx
  on public.user_play_logs (user_id, created_at desc);

alter table public.user_play_logs enable row level security;

create policy "Users can read own user_play_logs"
  on public.user_play_logs for select
  using (auth.uid() = user_id);

create or replace function public.log_play_event(
  p_event_id uuid,
  p_session_id uuid,
  p_song_id text,
  p_exercise_id text default null,
  p_play_mode text default 'challenge',
  p_event_type text default 'play_started',
  p_song_time_sec numeric default null,
  p_client_ts timestamptz default null,
  p_metadata jsonb default '{}'::jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.play_events_raw (
    event_id,
    session_id,
    user_id,
    song_id,
    exercise_id,
    play_mode,
    event_type,
    song_time_sec,
    client_ts,
    metadata
  ) values (
    p_event_id,
    p_session_id,
    auth.uid(),
    p_song_id,
    p_exercise_id,
    p_play_mode,
    p_event_type,
    p_song_time_sec,
    p_client_ts,
    coalesce(p_metadata, '{}'::jsonb)
  )
  on conflict (event_id) do nothing;
end;
$$;

grant execute on function public.log_play_event(uuid, uuid, text, text, text, text, numeric, timestamptz, jsonb) to authenticated;

create or replace function public.upsert_user_play_log(p_session_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user_id uuid;
  v_song_id text;
  v_exercise_id text;
  v_play_mode text;
  v_started_at timestamptz;
  v_ended_at timestamptz;
  v_events_count integer;
  v_terminal_event text;
  v_terminal_metadata jsonb;
  v_progress_max numeric;
  v_duration_target numeric;
  v_time_playing numeric;
  v_is_played_in_full boolean;
  v_exit_status text;
  v_days_since_signup numeric;
  v_user_created_at timestamptz;
begin
  select
    e.user_id,
    min(e.song_id),
    min(e.exercise_id),
    min(e.play_mode),
    min(e.created_at),
    max(e.created_at),
    count(*)::integer,
    max(e.song_time_sec),
    max(
      case
        when jsonb_typeof(e.metadata -> 'song_duration_sec') = 'number'
        then (e.metadata ->> 'song_duration_sec')::numeric
        else null
      end
    )
  into
    v_user_id,
    v_song_id,
    v_exercise_id,
    v_play_mode,
    v_started_at,
    v_ended_at,
    v_events_count,
    v_progress_max,
    v_duration_target
  from public.play_events_raw e
  where e.session_id = p_session_id
    and e.user_id = auth.uid();

  if v_user_id is null then
    return;
  end if;

  select e.event_type, e.metadata
  into v_terminal_event, v_terminal_metadata
  from public.play_events_raw e
  where e.session_id = p_session_id
    and e.user_id = auth.uid()
    and e.event_type in ('finished', 'exited', 'failed')
  order by e.created_at desc
  limit 1;

  v_time_playing := coalesce(
    case
      when jsonb_typeof(v_terminal_metadata -> 'time_playing_sec') = 'number'
      then (v_terminal_metadata ->> 'time_playing_sec')::numeric
      else null
    end,
    greatest(coalesce(v_progress_max, 0), 0)
  );

  v_is_played_in_full := coalesce(
    (v_terminal_event = 'finished'),
    false
  ) or (
    coalesce(v_duration_target, 0) > 0
    and coalesce(v_progress_max, 0) >= v_duration_target * 0.98
  );

  if v_terminal_event = 'finished' then
    v_exit_status := case
      when coalesce(v_terminal_metadata ->> 'success', 'false') = 'true' then 'succeeded'
      else 'failed'
    end;
  elsif v_terminal_event = 'exited' then
    v_exit_status := 'abandoned';
  elsif v_terminal_event = 'failed' then
    v_exit_status := 'failed';
  else
    v_exit_status := 'unknown';
  end if;

  select u.created_at into v_user_created_at
  from auth.users u
  where u.id = v_user_id;

  if v_user_created_at is not null then
    v_days_since_signup := extract(epoch from (v_started_at - v_user_created_at)) / 86400.0;
  else
    v_days_since_signup := null;
  end if;

  insert into public.user_play_logs (
    session_id,
    user_id,
    song_id,
    exercise_id,
    play_mode,
    days_since_signup,
    time_playing,
    is_played_in_full,
    exit_status,
    started_at,
    ended_at,
    events_count,
    updated_at
  ) values (
    p_session_id,
    v_user_id,
    v_song_id,
    v_exercise_id,
    v_play_mode,
    v_days_since_signup,
    v_time_playing,
    v_is_played_in_full,
    v_exit_status,
    v_started_at,
    v_ended_at,
    v_events_count,
    now()
  )
  on conflict (session_id) do update set
    days_since_signup = excluded.days_since_signup,
    time_playing = excluded.time_playing,
    is_played_in_full = excluded.is_played_in_full,
    exit_status = excluded.exit_status,
    ended_at = excluded.ended_at,
    events_count = excluded.events_count,
    updated_at = now();
end;
$$;

grant execute on function public.upsert_user_play_log(uuid) to authenticated;
