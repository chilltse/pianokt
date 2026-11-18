"""Stage durations only: never log credentials, URLs or request bodies."""
import logging
from contextvars import ContextVar
trace_id = ContextVar("upload_trace_id", default="job")
from contextlib import contextmanager
from time import perf_counter

@contextmanager
def stage(name):
    start = perf_counter()
    outcome = 'ok'
    try:
        yield
    except BaseException:
        outcome = 'error'
        raise
    finally:
        logging.getLogger('uvicorn.error').info('request_id=%s stage=%s duration_ms=%.1f outcome=%s', trace_id.get(), name, (perf_counter()-start)*1000, outcome)

# All labels are source-code constants. Never inspect SQL, parameters, rows or
# exception messages: those can contain tokens, URLs and personal data.
_sql_stats = ContextVar('sql_stats', default=None)


def event_count(name, count):
    logging.getLogger('uvicorn.error').info(
        'request_id=%s metric=%s count=%d', trace_id.get(), name, count)


@contextmanager
def sql_summary():
    stats = {}
    token = _sql_stats.set(stats)
    try:
        yield
    finally:
        _sql_stats.reset(token)
        for name, (count, total, maximum) in sorted(stats.items(), key=lambda item: item[1][1], reverse=True):
            logging.getLogger('uvicorn.error').info(
                'request_id=%s sql_summary=%s calls=%d total_ms=%.3f avg_ms=%.3f max_ms=%.3f',
                trace_id.get(), name, count, total, total / count, maximum)


@contextmanager
def sql_span(name):
    start = perf_counter()
    outcome = 'ok'
    try:
        yield
    except BaseException:
        outcome = 'error'
        raise
    finally:
        elapsed = (perf_counter() - start) * 1000
        stats = _sql_stats.get()
        if stats is not None:
            count, total, maximum = stats.get(name, (0, 0, 0))
            stats[name] = (count + 1, total + elapsed, max(maximum, elapsed))
        logging.getLogger('uvicorn.error').info(
            'request_id=%s sql=%s client_elapsed_ms=%.3f outcome=%s',
            trace_id.get(), name, elapsed, outcome)


def sql(connection, name, statement, parameters=None):
    # psycopg synchronous execute buffers the result before returning. Existing
    # fetch calls remain unchanged; elapsed includes server waits and transport.
    with sql_span(name):
        return connection.execute(statement, parameters)


class TimedExit:
    """Delegate the original context manager, timing its commit/rollback only.

    No additional SQL, transaction boundaries, retries or exception conversion.
    """
    def __init__(self, manager):
        self.manager = manager

    def __enter__(self):
        return self.manager.__enter__()

    def __exit__(self, exc_type, exc, traceback):
        with sql_span('transaction.commit' if exc_type is None else 'transaction.rollback'):
            return self.manager.__exit__(exc_type, exc, traceback)


def backend_identity(connection):
    # Driver metadata only: no extra round trip. Transaction-pooling proxies
    # may report a proxy PID, so observers must verify it before correlation.
    pid = getattr(getattr(connection, 'info', None), 'backend_pid', None)
    if isinstance(pid, int):
        event_count('db.backend_pid', pid)
