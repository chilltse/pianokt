-- Extend user_play_logs with terminal song position and recording link.
-- Also refresh aggregation function to populate the new fields.

alter table public.user_play_logs
  add column if not exists song_time_sec numeric;

alter table public.user_play_logs
  add column if not exists challenge_recording_id uuid;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'user_play_logs_challenge_recording_id_fkey'
  ) then
    alter table public.user_play_logs
      add constraint user_play_logs_challenge_recording_id_fkey
      foreign key (challenge_recording_id)
      references public.challenge_recordings (id)
      on delete set null;
  end if;
end $$;

create index if not exists user_play_logs_recording_idx
  on public.user_play_logs (challenge_recording_id);

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
  v_song_time_sec numeric;
  v_is_played_in_full boolean;
  v_exit_status text;
  v_days_since_signup numeric;
  v_user_created_at timestamptz;
  v_challenge_recording_id uuid;
begin
  v_user_id := auth.uid();
  if v_user_id is null then
    return;
  end if;

  select
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
    and e.user_id = v_user_id;

  if coalesce(v_events_count, 0) = 0 then
    return;
  end if;

  select e.event_type, e.metadata
  into v_terminal_event, v_terminal_metadata
  from public.play_events_raw e
  where e.session_id = p_session_id
    and e.user_id = v_user_id
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

  v_song_time_sec := coalesce(
    case
      when jsonb_typeof(v_terminal_metadata -> 'song_time_sec') = 'number'
      then (v_terminal_metadata ->> 'song_time_sec')::numeric
      else null
    end,
    greatest(coalesce(v_progress_max, 0), 0)
  );

  begin
    v_challenge_recording_id := nullif(v_terminal_metadata ->> 'challenge_recording_id', '')::uuid;
  exception
    when invalid_text_representation then
      v_challenge_recording_id := null;
  end;

  -- Fallback for historical/partial events where metadata did not carry recording ID.
  -- Pick the nearest challenge recording for the same user + song around this session window.
  if v_challenge_recording_id is null and v_play_mode = 'challenge' then
    select cr.id
    into v_challenge_recording_id
    from public.challenge_recordings cr
    where cr.user_id = v_user_id
      and cr.song_id = v_song_id
      and cr.created_at between v_started_at - interval '30 minutes'
                           and v_ended_at + interval '10 minutes'
    order by abs(extract(epoch from (cr.created_at - v_ended_at))) asc
    limit 1;
  end if;

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
    song_time_sec,
    challenge_recording_id,
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
    v_song_time_sec,
    v_challenge_recording_id,
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
    song_time_sec = excluded.song_time_sec,
    challenge_recording_id = excluded.challenge_recording_id,
    is_played_in_full = excluded.is_played_in_full,
    exit_status = excluded.exit_status,
    ended_at = excluded.ended_at,
    events_count = excluded.events_count,
    updated_at = now();
end;
$$;
