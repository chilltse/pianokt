begin;
-- A visit is a tab's stay on the site, independent of challenge/play cycles.
create table public.student_visits (
 id uuid primary key,
 user_id uuid not null references auth.users(id) on delete cascade,
 started_at timestamptz not null,
 last_seen_at timestamptz not null,
 ended_at timestamptz,
 end_reason text,
 end_is_inferred boolean not null default false,
 last_sequence bigint not null default 0 check(last_sequence >= 0),
 received_at timestamptz not null default now(),
 unique(id,user_id)
);
create index student_visits_order on public.student_visits(user_id,started_at,id);
-- Exists from actual playback start, even when no MIDI is ever finalized.
create table public.student_attempts (
 attempt_id uuid primary key,
 user_id uuid not null references auth.users(id) on delete cascade,
 visit_id uuid not null,
 visit_attempt_sequence bigint not null check(visit_attempt_sequence > 0),
 song_id text not null,
 song_source text not null,
 started_at timestamptz not null,
 ended_at timestamptz,
 end_reason text,
 received_at timestamptz not null default now(),
 foreign key(visit_id,user_id) references public.student_visits(id,user_id) on delete cascade,
 unique(visit_id,visit_attempt_sequence),
 unique(attempt_id,user_id)
);
create index student_attempts_order on public.student_attempts(user_id,started_at,attempt_id);
create table public.student_attempt_events (
 event_id uuid primary key,
 attempt_id uuid not null,
 user_id uuid not null,
 event_sequence integer not null check(event_sequence >= 0),
 event_type text not null check(event_type in ('play_started','paused','resumed','finished','exited','interrupted')),
 occurred_at timestamptz not null,
 song_time_sec double precision not null check(song_time_sec >= 0),
 elapsed_playing_sec double precision not null check(elapsed_playing_sec >= 0),
 metadata jsonb not null default '{}',
 received_at timestamptz not null default now(),
 foreign key(attempt_id,user_id) references public.student_attempts(attempt_id,user_id) on delete cascade,
 unique(attempt_id,event_sequence)
);
-- Missing close signals are estimates, never invented exact closure times.
create view public.student_visit_status with (security_invoker=true) as
 select v.*, coalesce(ended_at, case when last_seen_at < now()-interval '5 minutes' then last_seen_at end) as effective_ended_at,
 case when ended_at is not null then end_is_inferred else last_seen_at < now()-interval '5 minutes' end as effective_end_is_inferred
 from public.student_visits v;
alter table public.student_visits enable row level security;
alter table public.student_attempts enable row level security;
alter table public.student_attempt_events enable row level security;
create policy own_visits on public.student_visits for select to authenticated using(user_id=auth.uid());
create policy own_attempt_context on public.student_attempts for select to authenticated using(user_id=auth.uid());
create policy own_attempt_events on public.student_attempt_events for select to authenticated using(user_id=auth.uid());
revoke all on public.student_visits,public.student_attempts,public.student_attempt_events from anon,authenticated;
grant select on public.student_visits,public.student_attempts,public.student_attempt_events,public.student_visit_status to authenticated;
-- Backend-only lookup; no direct auth.users grant is needed by the exporter.
create function public.piano_registration_time(p_user_id uuid) returns timestamptz
language sql stable security definer set search_path=public,pg_temp
as $$ select created_at from auth.users where id=p_user_id $$;
revoke all on function public.piano_registration_time(uuid) from public,anon,authenticated;
do $$ begin
 if exists(select 1 from pg_roles where rolname='pianokt_backend') then
  grant select,insert,update on public.student_visits,public.student_attempts,public.student_attempt_events to pianokt_backend;
  grant select on public.student_visit_status to pianokt_backend;
  grant execute on function public.piano_registration_time(uuid) to pianokt_backend;
  create policy backend_visits on public.student_visits to pianokt_backend using(true) with check(true);
  create policy backend_attempt_context on public.student_attempts to pianokt_backend using(true) with check(true);
  create policy backend_attempt_events on public.student_attempt_events to pianokt_backend using(true) with check(true);
 end if;
end $$;
commit;
