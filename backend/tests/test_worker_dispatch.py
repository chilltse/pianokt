"""Duplicate ACKs keep leases/counters intact; due work still claims atomically."""
import base64
import json
import logging
import subprocess
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from test_activity import db, new_upload, full_packet, A, U
from test_pg_concurrency import postgres
from test_activity_batch import event_db, transaction
from pianokt_backend import worker, worker_api
from pianokt_backend.outbox_dispatch import recover_pending
from pianokt_backend.upload_service import commit_finalization
from pianokt_backend.upload_validation import InvalidUpload


@pytest.fixture
def accepted(event_db, monkeypatch):
    row = new_upload(event_db)
    with transaction(event_db): commit_finalization(event_db, row, U, full_packet())
    monkeypatch.setattr(worker, 'connect', lambda: transaction(event_db))
    monkeypatch.setenv('RAW_ROOT', '/unused-test-store')
    return event_db


@pytest.mark.parametrize('status', ['READY', 'INVALID', 'FAILED', 'VALIDATING', 'PROCESSING', 'RETRY'])
def test_duplicate_ack_does_not_claim_or_touch_files(accepted, monkeypatch, caplog, status):
    c = accepted
    token = uuid4()
    c.execute("update piano_attempts set status=%s,lease_token=%s,lease_until=now()+interval '10 minutes',next_retry_at=now()+interval '5 minutes',processing_attempts=3", (status, token))
    before = c.execute('select status,lease_token,lease_until,next_retry_at,processing_attempts from piano_attempts').fetchone()
    monkeypatch.setattr(worker, 'Objects', lambda *a: pytest.fail('duplicate started validation'))
    with caplog.at_level(logging.INFO): worker.process(A)
    assert 'stage=worker.duplicate outcome=ack' in caplog.text
    assert c.execute('select status,lease_token,lease_until,next_retry_at,processing_attempts from piano_attempts').fetchone() == before


@pytest.mark.parametrize('status', ['ACCEPTED', 'UPLOADED', 'RETRY', 'VALIDATING', 'PROCESSING'])
def test_due_and_expired_work_is_claimed(accepted, monkeypatch, status):
    c = accepted
    c.execute("update piano_attempts set status=%s,lease_until=now()-interval '1 minute',next_retry_at=now()-interval '1 minute',processing_attempts=2", (status,))
    def reject(*args): raise InvalidUpload('fixture invalid file')
    monkeypatch.setattr(worker, 'Objects', lambda *a: object())
    monkeypatch.setattr(worker, 'validate_files', reject)
    worker.process(A)
    row = c.execute('select status,processing_attempts from piano_attempts').fetchone()
    assert row == dict(status='INVALID', processing_attempts=3)


def test_created_or_missing_is_not_acknowledged(accepted):
    accepted.execute("update piano_attempts set status='CREATED'")
    with pytest.raises(RuntimeError, match='not finalized'): worker.process(A)
    with pytest.raises(RuntimeError, match='not finalized'): worker.process(str(uuid4()))


@pytest.mark.parametrize('status', ['RETRY', 'VALIDATING', 'PROCESSING'])
def test_recovery_exhaustion_and_due_reactivation(accepted, status):
    c = accepted
    c.execute("update piano_attempts set status=%s,processing_attempts=7,lease_until=now()-interval '1 minute',next_retry_at=now()-interval '1 minute'", (status,))
    c.execute("update piano_outbox set published_at=now()-interval '5 minutes'")
    with transaction(c): recover_pending(c)
    assert c.execute('select published_at from piano_outbox').fetchone()['published_at'] is None
    c.execute("update piano_attempts set processing_attempts=8")
    with transaction(c): recover_pending(c)
    assert c.execute('select status from piano_attempts').fetchone()['status'] == 'FAILED'


@pytest.mark.parametrize('kind', ['performance.submitted', 'performance.uploaded'])
@pytest.mark.parametrize('failure', [None, 'timeout', 'exit'])
def test_private_worker_preserves_subprocess_timeout_and_logs(monkeypatch, caplog, kind, failure):
    def run(command, *, check, timeout):
        assert command[-2:] == ['worker', A] and check and timeout == 480
        if failure == 'timeout': raise subprocess.TimeoutExpired(command, timeout)
        if failure == 'exit': raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(worker_api.subprocess, 'run', run)
    envelope = {'message': {'data': base64.b64encode(json.dumps({'event_type': kind, 'attempt_id': A}).encode()).decode()}}
    with caplog.at_level(logging.INFO, logger='uvicorn.error'):
        response = TestClient(worker_api.app).post('/pubsub', json=envelope)
    assert response.status_code == (503 if failure else 200)
    assert 'stage=worker.subprocess.total' in caplog.text


def test_duplicate_push_under_active_lease_returns_http_success(accepted, monkeypatch):
    accepted.execute("update piano_attempts set status='PROCESSING',lease_until=now()+interval '10 minutes',processing_attempts=1")
    monkeypatch.setattr(worker_api.subprocess, 'run', lambda command, **kw: worker.process(command[-1]))
    monkeypatch.setattr(worker, 'Objects', lambda *a: pytest.fail('duplicate started another worker'))
    data = base64.b64encode(json.dumps({'event_type': 'performance.submitted', 'attempt_id': A}).encode()).decode()
    assert TestClient(worker_api.app).post('/pubsub', json={'message': {'data': data}}).status_code == 200
    assert accepted.execute('select processing_attempts from piano_attempts').fetchone()['processing_attempts'] == 1
