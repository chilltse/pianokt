-- Run once as database administrator AFTER migrations through 018.
-- This role has explicit table grants and RLS policies, no BYPASSRLS or superuser.
create role pianokt_backend nologin;
grant usage on schema public to pianokt_backend;
grant select,insert,update on public.piano_attempts,public.piano_learner_keys,public.piano_outbox,
 public.piano_recommendations,public.piano_recommendation_feedback,public.challenge_recordings,
 public.user_play_logs to pianokt_backend;
grant select on public.play_events_raw to pianokt_backend;
grant select on public.piano_song_segments,public.piano_song_midis,public.piano_public_song_catalog,public.piano_song_catalog,public.piano_preferences to pianokt_backend;
create policy backend_attempts on public.piano_attempts to pianokt_backend using(true) with check(true);
create policy backend_keys on public.piano_learner_keys to pianokt_backend using(true) with check(true);
create policy backend_outbox on public.piano_outbox to pianokt_backend using(true) with check(true);
create policy backend_recommendations on public.piano_recommendations to pianokt_backend using(true) with check(true);
create policy backend_feedback on public.piano_recommendation_feedback to pianokt_backend using(true) with check(true);
create policy backend_recordings on public.challenge_recordings to pianokt_backend using(true) with check(true);
create policy backend_play_events on public.play_events_raw to pianokt_backend using(true) with check(true);
create policy backend_play_logs on public.user_play_logs to pianokt_backend using(true) with check(true);
create policy backend_catalog on public.piano_song_catalog for select to pianokt_backend using(true);
create policy backend_preferences on public.piano_preferences for select to pianokt_backend using(true);
-- Create a dedicated LOGIN role with a password via your secret-management workflow,
-- then GRANT pianokt_backend TO that_login. Never grant this role to anon/authenticated.

-- Migration 013 may run before this role is created on a fresh installation.
do $$ begin
 if to_regclass('public.student_visits') is not null then
  grant select,insert,update on public.student_visits,public.student_attempts,public.student_attempt_events to pianokt_backend;
  grant select on public.student_visit_status to pianokt_backend;
  grant execute on function public.piano_registration_time(uuid) to pianokt_backend;
  create policy backend_visits on public.student_visits to pianokt_backend using(true) with check(true);
  create policy backend_attempt_context on public.student_attempts to pianokt_backend using(true) with check(true);
  create policy backend_attempt_events on public.student_attempt_events to pianokt_backend using(true) with check(true);
 end if;
end $$;

create policy backend_midis_read on public.piano_song_midis for select to pianokt_backend using(true);

create policy backend_segments on public.piano_song_segments for select to pianokt_backend using(true);
