-- Historical accuracy_pct remains historical; only new event-accuracy writes
-- set accuracy_metric. Never backfill an event metric from old note scores.
alter table public.challenge_recordings
  add column if not exists frontend_note_accuracy numeric
    check (frontend_note_accuracy between 0 and 1),
  add column if not exists accuracy_metric text;
alter table public.challenge_recordings alter column accuracy_pct drop not null;
alter table public.challenge_recordings alter column accuracy_pct drop default;
comment on column public.challenge_recordings.accuracy_metric is
  'backend_event_accuracy identifies new event scores; NULL denotes legacy/unknown semantics';
