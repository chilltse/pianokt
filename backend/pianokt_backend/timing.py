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
