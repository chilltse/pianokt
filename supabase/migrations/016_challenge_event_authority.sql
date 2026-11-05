-- Apply before deploying the v2 API/frontend. Historical provenance is unknown,
-- never guessed production. No historical evidence is deleted or rewritten.
begin;
alter table public.student_visits add column source_environment text not null default 'unknown'
 check(source_environment in ('unknown','development','test','staging','production'));
alter table public.student_attempts add column source_environment text not null default 'unknown'
 check(source_environment in ('unknown','development','test','staging','production'));
alter table public.piano_attempts add column source_environment text not null default 'unknown'
 check(source_environment in ('unknown','development','test','staging','production'));
alter table public.challenge_recordings add column source_environment text not null default 'unknown'
 check(source_environment in ('unknown','development','test','staging','production'));
alter table public.student_attempts
 add column sealed_at timestamptz,
 add column sealed_event_count integer,
 add constraint student_attempt_seal_pair check (
   (sealed_at is null and sealed_event_count is null) or
   (sealed_at is not null and sealed_event_count is not null and sealed_event_count >= 2));
alter table public.user_play_logs
 add column completion_reason text check(completion_reason in ('finished','exited')),
 add column client_success boolean,
 add column projection_version integer not null default 1;
create index student_visits_environment on public.student_visits(source_environment,started_at);
create index student_attempts_environment on public.student_attempts(source_environment,started_at);
create index piano_attempts_export on public.piano_attempts(source_environment,result_committed_at) where status='READY';
-- Append the new column after existing view outputs (CREATE OR REPLACE cannot
-- insert a column in the middle of an already deployed view).
create or replace view public.student_visit_status with (security_invoker=true) as
 select id,user_id,started_at,last_seen_at,ended_at,end_reason,end_is_inferred,last_sequence,received_at,
 coalesce(ended_at,case when last_seen_at < now()-interval '5 minutes' then last_seen_at end) as effective_ended_at,
 case when ended_at is not null then end_is_inferred else last_seen_at < now()-interval '5 minutes' end as effective_end_is_inferred,
 source_environment from public.student_visits;

-- Challenge v2 has one event store. These objects remain read-only history.
drop trigger if exists piano_play_outbox on public.play_events_raw;
revoke execute on function public.log_play_event_atomic(uuid,uuid,text,text,text,text,numeric,timestamptz,jsonb) from public,anon,authenticated;
revoke execute on function public.log_play_event(uuid,uuid,text,text,text,text,numeric,timestamptz,jsonb) from public,anon,authenticated;
revoke execute on function public.upsert_user_play_log(uuid) from public,anon,authenticated;
-- Retire every historical save_challenge_recording overload as well: a
-- SECURITY DEFINER RPC could otherwise bypass the projection table grants.
do $$ declare signature regprocedure; begin
 for signature in select p.oid::regprocedure from pg_proc p
   join pg_namespace n on n.oid=p.pronamespace
   where n.nspname='public' and p.proname='save_challenge_recording'
 loop
  execute format('revoke execute on function %s from public,anon,authenticated', signature);
 end loop;
end $$;
do $$ begin
 if exists(select 1 from pg_roles where rolname='pianokt_backend') then
  revoke insert,update,delete on public.play_events_raw from pianokt_backend;
 end if;
end $$;
comment on table public.play_events_raw is 'Retired v1 event archive. New Challenge events live in student_attempt_events.';
comment on column public.user_play_logs.exit_status is 'Legacy-shaped client outcome; not the Worker alignment grade. See completion_reason/client_success.';
comment on column public.student_attempts.sealed_at is 'Full immutable v2 event sequence committed by finalize; identical event retries remain valid.';
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
    where cr.source_environment='production'
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



commit;
