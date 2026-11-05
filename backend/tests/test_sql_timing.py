"""Diagnostic output contains fixed labels and numeric counters only."""
import logging
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from pianokt_backend import timing


def test_sql_summary_counts_durations_and_never_logs_values(caplog, monkeypatch):
    ticks = iter([0, .1, .2, .5])
    monkeypatch.setattr(timing, 'perf_counter', lambda: next(ticks))
    secret = 'JWT-secret https://host/signed?signature=secret personal@example.com'
    calls = []
    cursor = object()
    def execute(statement, parameters):
        calls.append((statement, parameters))
        return cursor
    with caplog.at_level(logging.INFO, logger='uvicorn.error'), timing.sql_summary():
        for _ in range(2):
            assert timing.sql(SimpleNamespace(execute=execute), 'event.insert', secret, (secret,)) is cursor
        timing.event_count('activity.events', 2)
    assert len(calls) == 2
    assert secret not in caplog.text
    assert 'calls=2 total_ms=400.000 avg_ms=200.000 max_ms=300.000' in caplog.text
    assert 'metric=activity.events count=2' in caplog.text
    assert timing._sql_stats.get() is None


def test_failure_preserves_exception_and_records_rollback(caplog):
    secret = 'JWT-secret signed-url personal@example.com'
    error = ValueError(secret)
    @contextmanager
    def transaction():
        yield
    def execute(*args):
        raise error
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        with pytest.raises(ValueError) as raised:
            with timing.sql_summary(), timing.TimedExit(transaction()):
                timing.sql(SimpleNamespace(execute=execute), 'visit.insert', secret, (secret,))
    assert raised.value is error
    assert secret not in caplog.text
    assert 'sql=visit.insert' in caplog.text
    assert 'sql=transaction.rollback' in caplog.text
    assert 'outcome=error' in caplog.text


def test_commit_is_measured_and_original_exit_value_preserved(caplog):
    class Manager:
        def __enter__(self): return self
        def __exit__(self, *args): return True
    with caplog.at_level(logging.INFO, logger='uvicorn.error'), timing.sql_summary():
        with timing.TimedExit(Manager()):
            pass
    assert 'sql=transaction.commit' in caplog.text


def test_unknown_http_paths_do_not_leak_secrets(caplog):
    from fastapi.testclient import TestClient
    from pianokt_backend.api import app
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        # No lifespan: this route does not need DB configuration.
        TestClient(app).get('/private-person@example.com?signature=JWT-secret')
    assert 'private-person' not in caplog.text
    assert 'JWT-secret' not in caplog.text
    assert 'http.GET.other' in caplog.text
