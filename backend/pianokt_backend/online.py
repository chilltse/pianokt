import os
import psycopg
from contextlib import contextmanager
from threading import Lock
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from .timing import stage, sql, sql_summary, TimedExit, backend_identity

_pool = None
_pool_enabled = False
_pool_lock = Lock()

def pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(os.environ['DATABASE_URL'], min_size=0,
                max_size=int(os.getenv('DB_POOL_MAX_SIZE', '4')), timeout=10,
                kwargs=dict(row_factory=dict_row, connect_timeout=10, prepare_threshold=None,
                            options='-c extra_float_digits=1'), open=True)
        return _pool

def enable_pool():
    global _pool_enabled
    _pool_enabled = True

def close_pool():
    global _pool, _pool_enabled
    _pool_enabled = False
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None

@contextmanager
def connect():
    # Jobs/CLI keep dedicated connections, including session-level advisory locks.
    if not _pool_enabled:
        with sql_summary(), TimedExit(psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
                             connect_timeout=10, options='-c extra_float_digits=1')) as connection:
            backend_identity(connection)
            yield connection
        return
    current = pool()
    with stage('db.acquire'):
        connection = current.getconn()
    try:
        with sql_summary(), stage('db.transaction'):
            with TimedExit(connection.transaction()):
                backend_identity(connection)
                yield connection
    finally:
        current.putconn(connection)

def owned_attempt(c,attempt,uid):
    return sql(c, 'attempt.owned_select', 'select * from public.piano_attempts where id=%s and user_id=%s',(attempt,uid)).fetchone()
