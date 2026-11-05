# /finalize transaction diagnosis

Historical observations before the optimization:
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
event.batch_insert/batch_conflict_select and the sequential fallback labels
(event.insert/conflict_select/sealed_select), event.latest_select/count, seal.*,
projection.*, outbox.insert and transaction.commit/rollback. Summaries are per
transaction and fixed operation label, not SQL text.

New unsealed, valid packets with distinct event IDs and sequences use one
parameterized batch INSERT followed by one independent SELECT. Every input is
checked, including rows skipped by ON CONFLICT; a LEFT JOIN preserves missing
IDs, input ordinals preserve validation order, and floats are compared inside
PostgreSQL. Sealed, internally duplicated and malformed packets retain the
sequential path and original error precedence. The final persisted-event count,
sealing, projections and Outbox still share the original transaction.

finalize acquires the Attempt advisory lock once and calls the internal writer
only after validate_finalization confirms the activity belongs to that Attempt.
The public save_activity entry point still acquires its lock. The final UPDATE
returns the receipt with RETURNING; identical finalize retries return the
current locked row, including a later READY state and validated_at. The HTTP
response is still sent only after successful COMMIT.

Use finalize_waits.sql from a separate observer connection while reproducing.
Correlate db.backend_pid with observer PIDs and time windows. A transaction-pool
proxy can supply a proxy PID rather than the server PID; verify mapping first.
A missing sample does not prove absence of waits. Observer visibility depends
on database privileges; do not grant extra privileges from this script.

Same Attempt: /activity and /finalize serialize first on the advisory lock.
The former second acquisition inside finalize was reentrant and has been
removed; its historical 247 ms was not evidence of another contender on that
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

## Run the latency baseline

From the repository root, using the same .venv and backend/.env.local as the
local API:

```bash
bash scripts/db-roundtrip.sh --samples 40
```

The script uses online.pool() settings, explicitly makes the transaction
read-only and runs serial SELECT 1 calls on one connection. It reports initial
and reused pool acquisition, BEGIN, COMMIT, first SELECT, and steady-state
min/median/P95/average/max plus raw duration samples. SET TRANSACTION and the
local 10-second statement timeout setup are outside the SELECT samples. No
business tables, DSNs, parameters, query results or error details are logged.

In a deployed environment with DATABASE_URL already configured:

```bash
PYTHONPATH=backend python backend/diagnostics/db_roundtrip.py --samples 40
```

Compare the local and deployed API environments separately. A slow local
baseline alone does not establish that the deployed API is far from the DB.
SELECT 1 measures transport + proxy/queue + server + driver time, not pure
network RTT. Initial acquisition includes connection setup; reused acquisition
usually has no database network round trip. A long tail needs observer samples
and repeated measurements, even if the median is near 250 ms.

### Local baseline observed on 2026-11-05

Using backend/.env.local from this workstation, 40 SELECT 1 samples:
first 254.18 ms; the remaining 39 had median 247.16 ms, P95 708.34 ms,
average 301.95 ms and maximum 718.41 ms. Initial acquisition was 3,101.56 ms,
reused acquisition 0.062 ms, BEGIN 241.44 ms and COMMIT 278.02 ms.
This confirms a substantial local end-to-end cost even for trivial SQL, with
occasional latency spikes. It does not locate the delay within network/proxy/
server scheduling or establish deployed-environment latency. No business rows
were read or written by this probe.

## Verify a real request

Start the local API and retain logs:

```bash
bash scripts/dev-backend.sh 2>&1 | tee /tmp/pianokt-backend.log
```

Complete a new practice cycle with pause/resume events. Collect its generated
X-Request-ID and select only matching log records. For a normal first finalize
with nine events and no validated projection, expect:

- finalize.activity_events, activity.events and activity.stored_events: 9.
- event.batch_insert: calls=1; event.batch_conflict_select: calls=1.
- advisory.finalize: calls=1; advisory.activity absent from that request.
- attempt.owned_select: calls=1, rather than 2.
- transaction.commit: calls=1, outcome=ok; response 202, status ACCEPTED.
- 19 instrumented application SQL executions plus COMMIT = 20 operations,
  down from 37 + COMMIT = 38. BEGIN is included in db.transaction, not this count.

Compare equivalent first submissions and event counts; duplicate finalize is a
different path. Replay an identical finalize body: expect 202, unchanged event
and Outbox counts, and the current Attempt state (possibly READY). The retry
normally has three measured SQL statements plus COMMIT and no event writes.
A modified finalize body must return 409. A late identical /activity packet is
allowed; a new event after sealing must return 409. Sealed /activity intentionally
uses the sequential fallback, so its SQL count differs from first finalize.

Observe /activity during finalize with finalize_waits.sql. Waiting on the shared
Attempt/Visit is expected; shortening the transaction should shorten the window,
not remove serialization. Compare both total transaction time and lock samples.
At a steady 250 ms per operation, eliminating 18 operations would save roughly
4.5 seconds, but this is an estimate, not an observed end-to-end improvement.
GCS signing and frontend polling are unchanged in this patch.

## Automated regression tests

The CI backend job already supplies PostgreSQL 17 and PGlite. For the same
checks locally, start Docker and create an isolated test-only server:

```bash
docker run --detach --rm --name pianokt-finalize-regression \
  --publish 127.0.0.1:55432:5432 \
  --env POSTGRES_PASSWORD=pianokt_test postgres:17
npm install --prefix /private/tmp/pianokt-test-db --no-audit --no-fund @electric-sql/pglite@0.3.14
```

Wait until the test server is ready:

```bash
docker exec pianokt-finalize-regression pg_isready -U postgres
```

Run from the repository root (the DSN below must point to the isolated test
server; fixtures create and drop randomly named databases):

```bash
PGLITE_MODULE=/private/tmp/pianokt-test-db/node_modules/@electric-sql/pglite/dist/index.js \
PIANOKT_TEST_POSTGRES_DSN=postgresql://postgres:pianokt_test@127.0.0.1:55432/postgres \
PYTHONPATH=backend .venv/bin/python -m pytest \
  backend/tests/test_activity.py backend/tests/test_activity_batch.py \
  backend/tests/test_async_finalization.py backend/tests/test_pg_concurrency.py \
  backend/tests/test_db_roundtrip.py backend/tests/test_sql_timing.py \
  backend/tests/test_upload_performance.py backend/tests/test_online.py -q
```

The full backend suite passed locally after this change: 133 passed, one Spark
integration test skipped because its explicit gate was disabled. PGlite and
real PostgreSQL tests both ran.

Tests cover batch/new/replayed/out-of-order events, ID/sequence collisions,
request-internal duplicates, exact float identity, rollback after conflicts,
sealed late events, empty/1,000-event packets, receipt correctness and SQL count,
log privacy, and actual shared Visit/Attempt/unique-key waits in PostgreSQL.
The real concurrency suite proves the competing backend is blocked by the
expected PID before releasing the transaction. Missing test dependencies result
in skipped integration tests; skipped tests do not establish correctness.

Clean up the temporary server after testing:

```bash
docker stop pianokt-finalize-regression
```

This patch adds no RPC, schema changes or diagnostic queries on API connections.
Per-statement INFO logging adds overhead; use the same logging configuration
when comparing runs. Diagnostic logs omit SQL text, parameters, exception
contents, row values, JWTs, signed URLs and arbitrary HTTP paths.
