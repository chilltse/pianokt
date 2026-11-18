-- Run on a separate observer connection during reproduction. Read-only;
-- no query text, SQL parameters, user IDs or connection credentials selected.
-- Repeat every 100-250 ms externally; each sample is a point-in-time observation.
SELECT clock_timestamp() AS sampled_at, pid, state, wait_event_type, wait_event,
       clock_timestamp() - xact_start AS transaction_age,
       clock_timestamp() - query_start AS statement_age,
       pg_blocking_pids(pid) AS blocking_pids
FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid()
  AND backend_type = 'client backend' AND state <> 'idle';

-- A row-lock wait commonly appears as a transactionid wait. Include ungranted
-- locks and their blockers, plus granted relation locks to identify tables.
SELECT l.pid, l.locktype, l.mode, l.granted, l.relation::regclass AS relation,
       pg_blocking_pids(l.pid) AS blocking_pids
FROM pg_locks l
WHERE l.pid IN (
  SELECT pid FROM pg_stat_activity
  WHERE datname = current_database() AND state <> 'idle'
    AND pid <> pg_backend_pid()
)
ORDER BY l.pid, l.granted, l.locktype;

-- Optional, only if pg_stat_statements is already enabled. Take before/after
-- snapshots and subtract counters; do NOT reset shared statistics.
-- queryid identifies normalized statements without exposing query text.
-- SELECT queryid, calls, total_plan_time, total_exec_time, mean_exec_time,
--        max_exec_time, rows, shared_blks_hit, shared_blks_read,
--        blk_read_time, blk_write_time
-- FROM pg_stat_statements WHERE dbid = (SELECT oid FROM pg_database WHERE datname=current_database());
