"""Failure injection on committed Outbox delivery; never uses a real Publisher."""
from contextlib import contextmanager
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
import pytest

from test_activity import db, A, U, full_packet, new_upload
from test_pg_concurrency import postgres
from test_activity_batch import event_db, transaction
from pianokt_backend import api, outbox_dispatch as dispatch


class Publisher:
    def __init__(self):
        self.messages = []
        self.error = None
        self.on_publish = lambda: None

    def publish(self, topic, data, **kwargs):
        self.on_publish()
        self.messages.append((topic, data, kwargs))
        def ack(timeout):
            assert 0 <= timeout <= kwargs['timeout']
            if self.error: raise self.error
            return 'message-id'
        return SimpleNamespace(result=ack)


@pytest.fixture
def delivery(event_db, monkeypatch):
    new_upload(event_db)
    commits = []
    @contextmanager
    def connect():
        with transaction(event_db):
            yield event_db
        commits.append('committed')
    monkeypatch.setattr(api, 'connect', connect)
    monkeypatch.setattr(dispatch, 'connect', connect)
    monkeypatch.setenv('DIRECT_PUBSUB_ENABLED', 'true')
    monkeypatch.setenv('DIRECT_PUBSUB_TIMEOUT_S', '2')
    monkeypatch.setenv('EVENT_TOPIC', 'projects/test/topics/events')
    publisher = Publisher()
    monkeypatch.setattr(dispatch, 'publisher', lambda: publisher)
    api.app.dependency_overrides[api.user] = lambda: U
    try:
        yield event_db, TestClient(api.app, raise_server_exceptions=False), publisher, commits
    finally:
        api.app.dependency_overrides.clear()


def finalize(client, body=None):
    return client.post(f'/attempts/{A}/finalize', json=(body or full_packet()).model_dump(mode='json'))


def test_commit_then_publish_wire_contract_and_strict_retry(delivery):
    c, client, publisher, commits = delivery
    publisher.on_publish = lambda: (None if commits else pytest.fail('publish preceded COMMIT'))
    response = finalize(client)
    assert response.status_code == 202
    assert response.json() == dict(id=A, status='ACCEPTED', validated_at=None)
    row = c.execute('select * from piano_outbox').fetchone()
    topic, data, attrs = publisher.messages[0]
    assert topic == 'projects/test/topics/events'
    assert data == json.dumps(dict(row['payload'], event_id=str(row['event_id']), event_type=row['event_type'])).encode()
    assert attrs['event_type'] == 'performance.submitted' and attrs['retry'] is None
    assert row['published_at'] is not None
    assert finalize(client).status_code == 202
    assert len(publisher.messages) == 1
    changed = full_packet(); changed.activity.events[-1].metadata = {'tampered': True}
    assert finalize(client, changed).status_code == 409
    assert c.execute('select count(*) as n from piano_outbox').fetchone()['n'] == 1


@pytest.mark.parametrize('failure', ['timeout', 'transport'])
def test_publish_failure_is_still_accepted_and_relay_replays_same_event(delivery, failure, caplog):
    c, client, publisher, _ = delivery
    secret = 'JWT-secret postgresql://user:password@host signed-url?signature=secret'
    publisher.error = TimeoutError(secret) if failure == 'timeout' else RuntimeError(secret)
    with caplog.at_level(logging.INFO):
        assert finalize(client).status_code == 202
    assert secret not in caplog.text
    assert c.execute('select published_at from piano_outbox').fetchone()['published_at'] is None
    original = json.loads(publisher.messages[0][1])['event_id']
    publisher.error = None
    assert dispatch.relay(limit=1) == 1
    assert json.loads(publisher.messages[1][1])['event_id'] == original
    assert c.execute('select published_at from piano_outbox').fetchone()['published_at'] is not None


def test_main_commit_failure_never_calls_publisher(delivery, monkeypatch):
    c, client, publisher, _ = delivery
    @contextmanager
    def fail_commit():
        with transaction(c):
            yield c
            raise RuntimeError('injected commit failure')
    monkeypatch.setattr(api, 'connect', fail_commit)
    assert finalize(client).status_code == 500
    assert publisher.messages == []
    assert c.execute('select status from piano_attempts').fetchone()['status'] == 'CREATED'
    assert c.execute('select count(*) as n from piano_outbox').fetchone()['n'] == 0


@pytest.mark.parametrize('failure', ['sql', 'commit'])
def test_ack_without_confirmation_commit_can_be_replayed(delivery, monkeypatch, caplog, failure):
    c, client, publisher, _ = delivery
    original_connect = dispatch.connect
    class FailingConfirm:
        def execute(self, statement, params=None):
            if statement.startswith('update public.piano_outbox set published_at'):
                raise RuntimeError('injected secret-credential')
            return c.execute(statement, params)
    @contextmanager
    def fail_confirm():
        with transaction(c):
            yield FailingConfirm() if failure == 'sql' else c
            if failure == 'commit': raise RuntimeError('injected secret-credential')
    monkeypatch.setattr(dispatch, 'connect', fail_confirm)
    with caplog.at_level(logging.INFO):
        assert finalize(client).status_code == 202
    assert 'secret-credential' not in caplog.text
    assert 'outcome=confirmed' not in caplog.text
    assert c.execute('select published_at from piano_outbox').fetchone()['published_at'] is None
    assert len(publisher.messages) == 1  # ACK already happened.
    monkeypatch.setattr(dispatch, 'connect', original_connect)
    assert dispatch.relay(limit=1) == 1
    assert publisher.messages[0][1] == publisher.messages[1][1]


def test_disabled_fast_path_and_lost_process_window_remain_recoverable(delivery, monkeypatch):
    c, client, publisher, _ = delivery
    monkeypatch.delenv('DIRECT_PUBSUB_ENABLED', raising=False)
    assert finalize(client).status_code == 202
    assert publisher.messages == []
    # This is also the persisted state after an API exits between commit/publish.
    assert dispatch.relay(limit=1) == 1
    assert len(publisher.messages) == 1


def test_identical_finalize_can_retry_unpublished_event(delivery):
    c, client, publisher, _ = delivery
    publisher.error = TimeoutError()
    assert finalize(client).status_code == 202
    publisher.error = None
    assert finalize(client).status_code == 202
    assert publisher.messages[0][1] == publisher.messages[1][1]
    assert c.execute('select count(*) as n from piano_outbox').fetchone()['n'] == 1


def test_relay_keeps_other_event_types_and_obeys_limit(delivery, monkeypatch):
    c, _, publisher, _ = delivery
    ids = []
    for kind in ('performance.uploaded', 'alignment.completed', 'recommendation.feedback'):
        ids.append(c.execute('insert into piano_outbox(event_type,aggregate_id,payload) values(%s,%s,%s) returning event_id',
                             (kind, str(uuid4()), Jsonb({'event_type': kind, 'attempt_id': A}))).fetchone()['event_id'])
    assert dispatch.publish_outbox_event(ids[0]) is False  # Fast path is submitted-only.
    assert dispatch.relay(limit=2) == 2
    assert dispatch.relay(limit=2) == 1
    assert {m[2]['event_type'] for m in publisher.messages} == {'performance.uploaded', 'alignment.completed', 'recommendation.feedback'}
    assert dispatch.relay(limit=2) == 0


def test_relay_elapsed_budget_stops_starting_new_messages(delivery, monkeypatch):
    c, _, publisher, _ = delivery
    for _ in range(2):
        c.execute("insert into piano_outbox(event_type,aggregate_id,payload) values('alignment.completed',%s,'{}')", (str(uuid4()),))
    clock = [0.0]
    monkeypatch.setattr(dispatch, 'monotonic', lambda: clock[0])
    publisher.on_publish = lambda: clock.__setitem__(0, 6.0)
    assert dispatch.relay(limit=1000, max_duration_s=5) == 1
    assert c.execute('select count(*) as n from piano_outbox where published_at is null').fetchone()['n'] == 1


def test_idle_relay_does_not_initialize_cloud_publisher(event_db, monkeypatch):
    monkeypatch.setattr(dispatch, 'connect', lambda: transaction(event_db))
    monkeypatch.setattr(dispatch, 'publisher', lambda: pytest.fail('idle relay loaded publisher'))
    assert dispatch.relay() == 0


def test_publisher_is_shared_and_closed_once(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from google.cloud import pubsub_v1
    created, stopped = [], []
    def create():
        client = SimpleNamespace(stop=lambda: stopped.append('stop'), transport=SimpleNamespace(close=lambda: stopped.append('close')))
        created.append(client)
        return client
    monkeypatch.setattr(dispatch, '_publisher', None)
    monkeypatch.setattr(pubsub_v1, 'PublisherClient', create)
    with ThreadPoolExecutor(max_workers=8) as executor:
        clients = list(executor.map(lambda _: dispatch.publisher(), range(16)))
    assert len(created) == 1 and all(c is created[0] for c in clients)
    dispatch.close_publisher(); dispatch.close_publisher()
    assert stopped == ['stop', 'close']


def test_invalid_timeout_configuration_cannot_undo_acceptance(delivery, monkeypatch, caplog):
    c, client, publisher, _ = delivery
    monkeypatch.setenv('DIRECT_PUBSUB_TIMEOUT_S', 'secret-invalid-timeout')
    with caplog.at_level(logging.WARNING):
        assert finalize(client).status_code == 202
    assert 'secret-invalid-timeout' not in caplog.text
    assert publisher.messages == []
    assert c.execute('select published_at from piano_outbox').fetchone()['published_at'] is None
