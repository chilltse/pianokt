-- Apply with the API/worker release. DDL and RPC replacement commit together.
begin;
alter table public.piano_attempts add column if not exists finalization jsonb;
alter table public.piano_attempts add column if not exists lease_token uuid;
alter table public.challenge_recordings add column if not exists client_accuracy_pct numeric;
alter table public.challenge_recordings add column if not exists score_source text not null default 'legacy';
-- Clients cannot forge an authoritative aligned score via direct table writes.
revoke insert,update,delete on public.challenge_recordings from anon,authenticated;
alter table public.challenge_recordings add constraint challenge_score_source_check check (score_source in ('legacy','pending','aligned','rejected'));
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

  perform pg_advisory_xact_lock(hashtext(p_session_id::text));
  -- Backend-finalized attempts own their summary; legacy clients cannot rewrite it.
  if exists(select 1 from public.piano_attempts p where p.user_id=v_user_id and p.metadata->>'session_id'=p_session_id::text) then
    return;
  end if;
  select
    min(e.song_id),
    min(e.exercise_id),
    min(e.play_mode),
    min(coalesce(e.client_ts,e.created_at)),
    max(coalesce(e.client_ts,e.created_at)),
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
  order by coalesce(e.client_ts,e.created_at) desc,e.event_id desc
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

  v_is_played_in_full := coalesce(v_terminal_event = 'finished',false);

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
      avg(cr.accuracy_pct) filter (where cr.score_source = 'aligned') as accuracy_avg,
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


-- Compatibility RPC: raw append, trigger outbox and terminal aggregate share one transaction.
create or replace function public.log_play_event_atomic(
 p_event_id uuid,p_session_id uuid,p_song_id text,p_exercise_id text default null,
 p_play_mode text default 'challenge',p_event_type text default 'play_started',
 p_song_time_sec numeric default null,p_client_ts timestamptz default null,p_metadata jsonb default '{}'
) returns void language plpgsql security definer set search_path=public,pg_temp as $$
declare e public.play_events_raw%rowtype;
begin
 if auth.uid() is null then raise exception 'Authentication required'; end if;
 perform pg_advisory_xact_lock(hashtext(p_session_id::text));
 if exists(select 1 from public.piano_attempts p where p.metadata->>'session_id'=p_session_id::text) then
   raise exception 'Use attempt finalization for backend-managed cycles';
 end if;
 perform public.log_play_event(p_event_id,p_session_id,p_song_id,p_exercise_id,p_play_mode,p_event_type,p_song_time_sec,p_client_ts,p_metadata);
 select * into e from public.play_events_raw where event_id=p_event_id;
 if e.user_id is distinct from auth.uid() or e.session_id is distinct from p_session_id or e.song_id is distinct from p_song_id
    or e.exercise_id is distinct from p_exercise_id or e.play_mode is distinct from p_play_mode
    or e.event_type is distinct from p_event_type or e.song_time_sec is distinct from p_song_time_sec
    or e.client_ts is distinct from p_client_ts or e.metadata is distinct from coalesce(p_metadata,'{}') then
   raise exception 'Event identity conflict';
 end if;
 if p_event_type in ('finished','exited','failed') then perform public.upsert_user_play_log(p_session_id); end if;
end $$;
revoke all on function public.log_play_event_atomic(uuid,uuid,text,text,text,text,numeric,timestamptz,jsonb) from public;
grant execute on function public.log_play_event_atomic(uuid,uuid,text,text,text,text,numeric,timestamptz,jsonb) to authenticated;
-- New clients use the atomic wrapper; direct raw writes would bypass the boundary.
revoke insert,update,delete on public.play_events_raw,public.user_play_logs from anon,authenticated;
grant select on public.play_events_raw,public.user_play_logs to authenticated;
revoke execute on function public.log_play_event(uuid,uuid,text,text,text,text,numeric,timestamptz,jsonb) from public,anon,authenticated;

commit;
