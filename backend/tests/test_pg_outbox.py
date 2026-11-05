"""Real concurrent publishers and duplicate consumers on an isolated database."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
import json
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
import pytest
from test_pg_concurrency import postgres
from test_activity import new_upload, full_packet, A, U
from pianokt_backend import outbox_dispatch as dispatch, worker
from pianokt_backend.upload_service import commit_finalization
from pianokt_backend.upload_validation import InvalidUpload


@pytest.fixture
def committed(postgres, monkeypatch):
    @contextmanager
    def connect():
        with psycopg.connect(postgres, row_factory=dict_row) as c:
            yield c
    monkeypatch.setattr(dispatch, 'connect', connect)
    monkeypatch.setattr(worker, 'connect', connect)
    monkeypatch.setenv('EVENT_TOPIC', 'projects/test/topics/events')
    with connect() as c:
        result = commit_finalization(c, new_upload(c), U, full_packet())
    return result['outbox_event_id'], connect


@pytest.mark.parametrize('first', ['api', 'relay'])
def test_api_and_relay_share_row_lock_without_losing_event(committed, monkeypatch, first):
    event_id, connect = committed
    started, release = Event(), Event()
    messages = []
    class Publisher:
        def publish(self, topic, data, **kwargs):
            # Independent connection proves the business transaction committed.
            with connect() as observer:
                assert observer.execute('select status from piano_attempts where id=%s', (A,)).fetchone()['status'] == 'ACCEPTED'
                assert observer.execute('select count(*) as n from piano_outbox').fetchone()['n'] == 1
            messages.append(json.loads(data))
            def ack(timeout):
                started.set()
                assert release.wait(5)
                return 'ack'
            return SimpleNamespace(result=ack)
    monkeypatch.setattr(dispatch, 'publisher', lambda: Publisher())
    direct = lambda: dispatch.publish_outbox_event(event_id, timeout_s=10)
    relay = lambda: dispatch.relay(limit=1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(direct if first == 'api' else relay)
        try:
            assert started.wait(5)
            second = executor.submit(relay if first == 'api' else direct)
            assert not second.result(timeout=3)  # SKIP LOCKED, not another publish.
        finally:
            release.set()
        assert future.result(timeout=5)
    assert len(messages) == 1 and messages[0]['event_id'] == str(event_id)
    with connect() as c:
        assert c.execute('select published_at from piano_outbox').fetchone()['published_at'] is not None


def test_duplicate_worker_ack_does_not_repeat_validation_or_increment_counter(committed, monkeypatch):
    _, connect = committed
    entered, release = Event(), Event()
    calls = []
    monkeypatch.setenv('RAW_ROOT', '/unused')
    monkeypatch.setattr(worker, 'Objects', lambda *args: object())
    def validate(*args):
        calls.append('validate')
        entered.set()
        assert release.wait(5)
        raise InvalidUpload('fixture invalid file')
    monkeypatch.setattr(worker, 'validate_files', validate)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(worker.process, A)
        try:
            assert entered.wait(5)
            duplicate = executor.submit(worker.process, A)
            assert duplicate.result(timeout=3) is None
            with connect() as c:
                row = c.execute('select status,processing_attempts from piano_attempts').fetchone()
                assert row == dict(status='VALIDATING', processing_attempts=1)
        finally:
            release.set()
        first.result(timeout=5)
    assert calls == ['validate']
    with connect() as c:
        assert c.execute('select processing_attempts from piano_attempts').fetchone()['processing_attempts'] == 1


def test_migrations_provide_required_outbox_uniqueness_and_async_columns(postgres):
    with psycopg.connect(postgres) as c:
        definitions = [r[0] for r in c.execute("select pg_get_constraintdef(oid) from pg_constraint where conrelid='public.piano_outbox'::regclass").fetchall()]
        assert 'PRIMARY KEY (event_id)' in definitions
        assert 'UNIQUE (event_type, aggregate_id)' in definitions
        columns = {r[0] for r in c.execute("select column_name from information_schema.columns where table_schema='public' and table_name='piano_attempts'").fetchall()}
        assert {'accepted_at','validated_at','file_generations','processing_attempts','next_retry_at','validation_error','lease_token','lease_until'} <= columns
