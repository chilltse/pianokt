-- Extend user_play_logs with challenge recording fields and retire challenge_recordings.

alter table public.user_play_logs
  add column if not exists song_time_sec numeric,
  add column if not exists song_source text,
  add column if not exists song_title text,
  add column if not exists duration_sec numeric,
  add column if not exists midi_storage_path text,
  add column if not exists accuracy_pct numeric,
  add column if not exists difficulty numeric default 0,
  add column if not exists midi_keyboard_used boolean default false;

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
  v_song_source text;
  v_song_title text;
  v_duration_sec numeric;
  v_midi_storage_path text;
  v_accuracy_pct numeric;
  v_difficulty numeric;
  v_midi_keyboard_used boolean;
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

  v_song_time_sec := greatest(coalesce(v_progress_max, 0), 0);

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

  v_song_source := nullif(v_terminal_metadata ->> 'song_source', '');
  v_song_title := nullif(v_terminal_metadata ->> 'song_title', '');
  v_midi_storage_path := nullif(v_terminal_metadata ->> 'midi_storage_path', '');

  v_duration_sec := coalesce(
    case
      when jsonb_typeof(v_terminal_metadata -> 'song_duration_sec') = 'number'
      then (v_terminal_metadata ->> 'song_duration_sec')::numeric
      else null
    end,
    v_duration_target
  );

  v_accuracy_pct := case
    when jsonb_typeof(v_terminal_metadata -> 'accuracy_pct') = 'number'
    then (v_terminal_metadata ->> 'accuracy_pct')::numeric
    else null
  end;

  v_difficulty := coalesce(
    case
      when jsonb_typeof(v_terminal_metadata -> 'difficulty') = 'number'
      then (v_terminal_metadata ->> 'difficulty')::numeric
      else null
    end,
    0
  );

  v_midi_keyboard_used := coalesce(
    case
      when jsonb_typeof(v_terminal_metadata -> 'midi_keyboard_used') = 'boolean'
      then (v_terminal_metadata ->> 'midi_keyboard_used')::boolean
      else null
    end,
    false
  );

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
    is_played_in_full,
    exit_status,
    started_at,
    ended_at,
    events_count,
    song_source,
    song_title,
    duration_sec,
    midi_storage_path,
    accuracy_pct,
    difficulty,
    midi_keyboard_used,
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
    v_is_played_in_full,
    v_exit_status,
    v_started_at,
    v_ended_at,
    v_events_count,
    v_song_source,
    v_song_title,
    v_duration_sec,
    v_midi_storage_path,
    v_accuracy_pct,
    v_difficulty,
    v_midi_keyboard_used,
    now()
  )
  on conflict (session_id) do update set
    days_since_signup = excluded.days_since_signup,
    time_playing = excluded.time_playing,
    song_time_sec = excluded.song_time_sec,
    is_played_in_full = excluded.is_played_in_full,
    exit_status = excluded.exit_status,
    ended_at = excluded.ended_at,
    events_count = excluded.events_count,
    song_source = excluded.song_source,
    song_title = excluded.song_title,
    duration_sec = excluded.duration_sec,
    midi_storage_path = excluded.midi_storage_path,
    accuracy_pct = excluded.accuracy_pct,
    difficulty = excluded.difficulty,
    midi_keyboard_used = excluded.midi_keyboard_used,
    updated_at = now();
end;
$$;

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
      upl.user_id,
      count(*)::bigint as challenge_count,
      avg(upl.accuracy_pct) as accuracy_avg,
      coalesce(max(upl.difficulty), 0)::numeric as max_difficulty
    from public.user_play_logs upl
    where upl.play_mode = 'challenge'
    group by upl.user_id
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

drop function if exists public.save_challenge_recording(uuid, text, text, text, numeric, text, numeric, numeric, boolean);
drop function if exists public.save_challenge_recording(uuid, text, text, text, numeric, text, numeric, numeric);

drop policy if exists "Users can read own challenge_recordings" on public.challenge_recordings;
drop policy if exists "Users can insert own challenge_recordings" on public.challenge_recordings;

drop table if exists public.challenge_recordings;
