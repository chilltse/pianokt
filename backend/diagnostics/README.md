# /finalize transaction diagnosis

No production timing has been measured by this change. Existing observations:
transaction 16,244 ms; advisory calls 1,372 + 247 = 1,619 ms (9.97%);
unattributed transaction time 14,625 ms (90.03%). Auth 513 ms and acquire 1.4 ms
are outside the transaction. Do not interpret advisory elapsed as lock wait.

Reproduce a fresh finalize, an identical retry, and concurrent /activity plus
/finalize. Collect uvicorn.error INFO logs by generated request_id. Each SQL
has client_elapsed_ms and outcome; sql_summary is sorted by total duration and
contains calls, total_ms, avg_ms and max_ms per label, including failed calls.
activity.events is the received event count, finalize.activity_events also
covers idempotent early returns; activity.stored_events is the persisted count
from the existing consistency check (not emitted on early return).
transaction.commit/rollback times the existing context manager exit. Pooled
connections expose db.transaction; its difference from SQL totals includes
BEGIN, Python validation, fetch conversion and instrumentation overhead.
No per-statement server timing is inferred from these client measurements.

Labels distinguish advisory.activity/finalize, row_lock.visit/activity_attempt/
piano_attempt/projection_session, visit.insert/update, attempt.insert/update,
event.insert/conflict_select/sealed_select/latest_select/count, seal.*,
projection.*, outbox.insert and transaction.commit/rollback. The event conflict
SELECT runs after every INSERT, including newly inserted events. Summaries are
per transaction and fixed operation label, not SQL text. Event SQL grows by two
serial round trips per received event in the normal unsealed path.

Use finalize_waits.sql from a separate observer connection while reproducing.
Correlate db.backend_pid with observer PIDs and time windows. A transaction-pool
proxy can supply a proxy PID rather than the server PID; verify mapping first.
A missing sample does not prove absence of waits. Observer visibility depends
on database privileges; do not grant extra privileges from this script.

Same Attempt: /activity and /finalize serialize first on the advisory lock;
the second acquisition inside finalize is reentrant. The observed 247 ms can
therefore reflect transport/server scheduling, not another contender on that
same advisory lock. Worker/rebuild writers also acquire this lock. The explicit
piano_attempts and student_attempts row locks can still wait on other writers.
Same Visit, different Attempts: different advisory locks do not protect the
shared student_visits row. Visit INSERT conflict checking or FOR UPDATE/update
may wait until another transaction commits. Visit-only /activity takes no
Attempt advisory lock. Learner INSERT, unique constraints, projections and
Outbox writes can also wait; look beyond SELECT FOR UPDATE.

pg_stat_activity Lock samples plus pg_blocking_pids establish active blocking;
transactionid waits often represent row contention. Optional pg_stat_statements
before/after deltas give aggregate server execution time, including execution
waits, and must be isolated from other traffic. They cannot precisely partition
one request into network/CPU/lock time. Client elapsed minus server aggregates
is only a rough transport/driver/queue residual, not an exact network RTT.
Do not enable SQL/parameter logging or use EXPLAIN ANALYZE on live writes.

Optimize only after sorting the measured summaries:
- If event INSERT + conflict SELECT dominate without observed waits, evaluate
  batching these two operations while preserving all identity checks and errors.
- If Visit contention dominates, reduce redundant client heartbeat overlap or
  coalesce queued heartbeats while retaining sequence and terminal semantics.
- If broad small-query latency dominates, check application/DB region and pool
  endpoint placement before changing transaction SQL.
- If projection or COMMIT dominates, investigate its blockers, triggers/indexes
  or server durability metrics first. Retain the atomic handoff and all locks.

This patch adds no RPC, extra diagnostic SQL on request connections, retries,
lock changes or schema changes. Per-statement INFO logging adds overhead; use
short controlled measurement windows and retain the same logging configuration
when comparing runs. Logs omit SQL text, parameters, exception contents, row
values, attempt/visit/user IDs, JWTs, signed URLs and arbitrary HTTP paths.
