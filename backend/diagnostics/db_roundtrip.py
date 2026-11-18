"""Read-only client latency baseline using the API's connection pool settings.

Run via scripts/db-roundtrip.sh locally, or with DATABASE_URL already exported:
PYTHONPATH=backend python backend/diagnostics/db_roundtrip.py --samples 40
Only numeric measurements are emitted. Never print a DSN or exception message.
"""
import argparse
import json
import logging
import math
import os
from statistics import mean, median
from time import perf_counter

from pianokt_backend import online


def timed(operation):
    start = perf_counter()
    result = operation()
    return result, (perf_counter() - start) * 1000


def summarize(samples):
    ordered = sorted(samples)
    return dict(count=len(samples), min_ms=min(samples), median_ms=median(samples),
                p95_ms=ordered[math.ceil(.95 * len(samples)) - 1],
                avg_ms=mean(samples), max_ms=max(samples))


class TransactionTimer:
    def __init__(self, connection, metrics):
        self.manager = connection.transaction()
        self.metrics = metrics

    def __enter__(self):
        result, self.metrics['begin_ms'] = timed(self.manager.__enter__)
        return result

    def __exit__(self, *exception):
        result, elapsed = timed(lambda: self.manager.__exit__(*exception))
        self.metrics['commit_ms' if exception[0] is None else 'rollback_ms'] = elapsed
        return result


def probe(pool, samples):
    connection, acquire_ms = timed(pool.getconn)
    metrics = dict(acquire_initial_ms=acquire_ms)
    try:
        # The same warmed connection is used for every SELECT. Use precisely
        # the API's transaction manager; BEGIN is timed before the first query.
        with TransactionTimer(connection, metrics):
            connection.execute('SET TRANSACTION READ ONLY')
            connection.execute("SET LOCAL statement_timeout = '10s'")
            durations = []
            for _ in range(samples):
                _, elapsed = timed(lambda: connection.execute('SELECT 1').fetchone())
                durations.append(elapsed)
    finally:
        pool.putconn(connection)
    # A second checkout distinguishes reusing the pool from initial connection
    # setup. It performs no SQL and does not measure a network round trip.
    connection, reuse_ms = timed(pool.getconn)
    pool.putconn(connection)
    return dict(metrics, acquire_reused_ms=reuse_ms,
                select_first_ms=durations[0], select_steady=summarize(durations[1:]),
                select_samples_ms=durations)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=40)
    args = parser.parse_args(argv)
    if not 2 <= args.samples <= 200:
        parser.error('--samples must be between 2 and 200')
    if not os.getenv('DATABASE_URL'):
        print('DATABASE_URL is not configured')
        return 1
    # Pool retry logs can contain hostnames/connection errors. Suppress them in
    # this standalone process only; API logging is unaffected.
    previous_disabled = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        print(json.dumps(probe(online.pool(), args.samples), indent=2))
        return 0
    except Exception:
        print('Database baseline failed; check connectivity and database configuration. No connection details logged.')
        return 1
    finally:
        online.close_pool()
        logging.disable(previous_disabled)


if __name__ == '__main__':
    raise SystemExit(main())
