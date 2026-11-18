-- An attempt is only finalized after its two MIDI blobs and this immutable
-- per-key telemetry sidecar have all been verified.  Nullable columns keep
-- historical attempts and older clients readable while the new client rolls
-- out.
alter table public.piano_attempts
  add column if not exists performance_events_key text,
  add column if not exists performance_events_hash text,
  add column if not exists performance_events_count integer;

alter table public.piano_attempts
  drop constraint if exists piano_attempts_performance_events_hash_format;

alter table public.piano_attempts
  add constraint piano_attempts_performance_events_hash_format
  check (
    performance_events_hash is null
    or performance_events_hash ~ '^[0-9a-f]{64}$'
  );

alter table public.piano_attempts
  drop constraint if exists piano_attempts_performance_events_count_range;

alter table public.piano_attempts
  add constraint piano_attempts_performance_events_count_range
  check (
    performance_events_count is null
    or performance_events_count between 0 and 50000
  );

-- Treat the sidecar identity as one immutable bundle. Historical attempts
-- legitimately have all three fields NULL; a partial identity must never be
-- eligible for finalization or worker processing.
alter table public.piano_attempts
  drop constraint if exists piano_attempts_performance_events_bundle;

alter table public.piano_attempts
  add constraint piano_attempts_performance_events_bundle
  check (
    (performance_events_key is null)
    = (performance_events_hash is null)
    and (performance_events_hash is null)
    = (performance_events_count is null)
  );
