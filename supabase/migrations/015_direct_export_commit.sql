-- Current READY pointer is the only direct-export commit authority.
-- No historical backfill: an object existing is not proof of a DB commit.
begin;
alter table public.piano_attempts
    add column if not exists result_digest text,
    add column if not exists result_committed_at timestamptz;
commit;
