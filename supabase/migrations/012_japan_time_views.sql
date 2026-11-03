-- Keep every source timestamp as timestamptz/UTC. These read-only views make
-- the Japanese business calendar explicit for Supabase dashboards and ops
-- queries, without storing ambiguous local timestamps in the base tables.

create or replace view public.v_challenge_recordings_jst
with (security_invoker = true) as
select
  r.*,
  r.created_at at time zone 'Asia/Tokyo' as created_at_jst,
  (r.created_at at time zone 'Asia/Tokyo')::date as created_date_jst
from public.challenge_recordings r;

create or replace view public.v_piano_attempts_jst
with (security_invoker = true) as
select
  a.*,
  a.created_at at time zone 'Asia/Tokyo' as created_at_jst,
  a.updated_at at time zone 'Asia/Tokyo' as updated_at_jst,
  (a.created_at at time zone 'Asia/Tokyo')::date as created_date_jst
from public.piano_attempts a;

create or replace view public.v_piano_outbox_jst
with (security_invoker = true) as
select
  o.*,
  o.created_at at time zone 'Asia/Tokyo' as created_at_jst,
  o.published_at at time zone 'Asia/Tokyo' as published_at_jst,
  (o.created_at at time zone 'Asia/Tokyo')::date as created_date_jst
from public.piano_outbox o;

create or replace view public.v_play_events_raw_jst
with (security_invoker = true) as
select
  e.*,
  e.client_ts at time zone 'Asia/Tokyo' as client_ts_jst,
  e.created_at at time zone 'Asia/Tokyo' as created_at_jst,
  (coalesce(e.client_ts, e.created_at) at time zone 'Asia/Tokyo')::date as event_date_jst
from public.play_events_raw e;

create or replace view public.v_user_play_logs_jst
with (security_invoker = true) as
select
  l.*,
  l.started_at at time zone 'Asia/Tokyo' as started_at_jst,
  l.ended_at at time zone 'Asia/Tokyo' as ended_at_jst,
  l.created_at at time zone 'Asia/Tokyo' as created_at_jst,
  l.updated_at at time zone 'Asia/Tokyo' as updated_at_jst,
  (l.started_at at time zone 'Asia/Tokyo')::date as started_date_jst
from public.user_play_logs l;

comment on view public.v_piano_attempts_jst is
  'Japan-time dashboard projection; base timestamptz fields remain canonical UTC.';
