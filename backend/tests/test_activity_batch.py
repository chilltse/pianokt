"""Batch SQL and receipts, exercised on PGlite and real PostgreSQL when enabled."""
from contextlib import contextmanager
import logging
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import psycopg
from psycopg.rows import dict_row
import pytest

from test_activity import db, packet, full_packet, new_upload, A, U
from test_pg_concurrency import postgres
from pianokt_backend import api, activity
from pianokt_backend.timing import sql_summary
from pianokt_backend.upload_service import commit_finalization


@pytest.fixture(params=['pglite', 'postgres'])
def event_db(request):
    if request.param == 'pglite':
        yield request.getfixturevalue('db')
    else:
        with psycopg.connect(request.getfixturevalue('postgres'), row_factory=dict_row, autocommit=True) as c:
            yield c


@contextmanager
def transaction(c):
    c.execute('begin')
    try:
        yield c
        c.execute('commit')
    except BaseException:
        c.execute('rollback')
        raise


def nine_events():
    body = full_packet()
    body.activity.events = [packet(event_sequence=i, event_type=(
        'play_started' if i == 0 else 'finished' if i == 8 else 'paused' if i % 2 else 'resumed'
    )).events[0] for i in range(9)]
    return body


def test_nine_event_finalize_receipt_sql_count_and_retry(event_db, monkeypatch, caplog):
    new_upload(event_db)
    monkeypatch.setattr(api, 'connect', lambda: transaction(event_db))
    api.app.dependency_overrides[api.user] = lambda: U
    body = nine_events().model_dump(mode='json')
    try:
        client = TestClient(api.app)
        with caplog.at_level(logging.INFO, logger='uvicorn.error'):
            response = client.post(f'/attempts/{A}/finalize', json=body)
        assert response.status_code == 202
        assert response.json() == dict(id=A, status='ACCEPTED', validated_at=None)
        messages = [r.getMessage() for r in caplog.records]
        # 19 measured application statements; the production context adds COMMIT.
        assert sum(' sql=' in m for m in messages) == 19
        for label in ('advisory.finalize', 'attempt.owned_select', 'event.batch_insert', 'event.batch_conflict_select'):
            assert sum(f' sql={label} ' in m for m in messages) == 1
        assert not any(' sql=advisory.activity ' in m or ' sql=event.insert ' in m for m in messages)
        assert event_db.execute('select count(*) as n from student_attempt_events').fetchone()['n'] == 9
        assert event_db.execute('select sealed_event_count from student_attempts').fetchone()['sealed_event_count'] == 9
        assert event_db.execute('select count(*) as n from piano_outbox').fetchone()['n'] == 1
        # Retry returns the current locked state, not a fabricated ACCEPTED value.
        event_db.execute("update piano_attempts set status='READY',validated_at=now()")
        caplog.clear()
        with caplog.at_level(logging.INFO, logger='uvicorn.error'):
            retry = client.post(f'/attempts/{A}/finalize', json=body)
        assert retry.status_code == 202 and retry.json()['status'] == 'READY'
        assert retry.json()['validated_at'] is not None
        assert 'sql=event.batch_insert ' not in caplog.text
        assert event_db.execute('select count(*) as n from piano_outbox').fetchone()['n'] == 1
    finally:
        api.app.dependency_overrides.clear()


def test_batch_reorders_replays_and_checks_every_input(event_db, caplog):
    body = nine_events().activity
    first = body.model_copy(deep=True)
    first.events = [body.events[0], body.events[4]]
    with transaction(event_db): activity.save_activity(event_db, U, first)
    body.events.reverse()
    with caplog.at_level(logging.INFO, logger='uvicorn.error'), sql_summary(), transaction(event_db):
        activity.save_activity(event_db, U, body)
    assert 'sql_summary=event.batch_insert calls=1' in caplog.text
    assert 'sql_summary=event.batch_conflict_select calls=1' in caplog.text
    assert event_db.execute('select end_reason from student_attempts').fetchone()['end_reason'] == 'finished'
    assert event_db.execute('select count(*) as n from student_attempt_events').fetchone()['n'] == 9


@pytest.mark.parametrize('field,value', [
    ('metadata', {'private': 'JWT-secret https://signed.invalid/?signature=secret'}),
    ('song_time_sec', 0.123456789), ('elapsed_playing_sec', 0.123456789),
    ('event_type', 'exited'), ('event_sequence', 20),
])
def test_batch_conflict_rolls_back_new_events_and_visit(event_db, field, value, caplog):
    original = packet(event_sequence=1, event_type='paused')
    with transaction(event_db): activity.save_activity(event_db, U, original)
    body = nine_events().activity
    body.visit.sequence = 99
    setattr(body.events[1], field, value)
    with caplog.at_level(logging.INFO), pytest.raises(HTTPException) as error:
        with transaction(event_db): activity.save_activity(event_db, U, body)
    assert error.value.status_code == 409
    assert event_db.execute('select count(*) as n from student_attempt_events').fetchone()['n'] == 1
    assert event_db.execute('select last_sequence from student_visits').fetchone()['last_sequence'] == 1
    assert 'JWT-secret' not in caplog.text and 'signed.invalid' not in caplog.text


def test_sequence_conflict_with_missing_id_is_not_lost_by_join(event_db):
    with transaction(event_db): activity.save_activity(event_db, U, packet())
    body = full_packet().activity
    body.events[0].event_id = uuid4()
    with pytest.raises(HTTPException, match='identity conflict'):
        with transaction(event_db): activity.save_activity(event_db, U, body)
    assert event_db.execute('select count(*) as n from student_attempt_events').fetchone()['n'] == 1


def test_cross_attempt_event_id_cannot_be_rebound(event_db):
    with transaction(event_db): activity.save_activity(event_db, U, packet())
    body = packet(attempt=str(uuid4()))
    with pytest.raises(HTTPException, match='identity conflict'):
        with transaction(event_db): activity.save_activity(event_db, U, body)
    assert event_db.execute('select count(*) as n from student_attempts').fetchone()['n'] == 1


@pytest.mark.parametrize('duplicate', ['identical', 'changed', 'sequence'])
def test_request_internal_duplicates_keep_original_behavior(event_db, duplicate):
    body = packet()
    second = body.events[0].model_copy(deep=True)
    if duplicate == 'changed': second.metadata = {'changed': True}
    if duplicate == 'sequence': second.event_id = uuid4()
    body.events.append(second)
    if duplicate == 'identical':
        with transaction(event_db): activity.save_activity(event_db, U, body)
        assert event_db.execute('select count(*) as n from student_attempt_events').fetchone()['n'] == 1
    else:
        with pytest.raises(HTTPException, match='identity conflict'):
            with transaction(event_db): activity.save_activity(event_db, U, body)
        assert event_db.execute('select count(*) as n from student_attempt_events').fetchone()['n'] == 0


def test_earlier_identity_conflict_precedes_later_validation_error(event_db):
    with transaction(event_db): activity.save_activity(event_db, U, packet())
    body = full_packet().activity
    body.events[0].metadata = {'changed': True}
    body.events[1].metadata = {'oversize': 'x' * 16001}
    with pytest.raises(HTTPException) as error:
        with transaction(event_db): activity.save_activity(event_db, U, body)
    assert error.value.status_code == 409
    body.events[0].metadata = {}
    with pytest.raises(HTTPException) as error:
        with transaction(event_db): activity.save_activity(event_db, U, body)
    assert error.value.status_code == 422


def test_sealed_replay_and_new_event_are_still_distinguished(event_db):
    body = full_packet()
    row = new_upload(event_db)
    with transaction(event_db): commit_finalization(event_db, row, U, body)
    with transaction(event_db): activity.save_activity(event_db, U, body.activity)
    extra = packet(event_sequence=2, event_type='interrupted')
    with pytest.raises(HTTPException, match='immutable'):
        with transaction(event_db): activity.save_activity(event_db, U, extra)
    assert event_db.execute('select sealed_event_count from student_attempts').fetchone()['sealed_event_count'] == 2


def test_empty_and_maximum_size_packets(event_db):
    body = packet()
    body.events = []
    with transaction(event_db): activity.save_activity(event_db, U, body)
    assert event_db.execute('select count(*) as n from student_attempt_events').fetchone()['n'] == 0
    template = packet(event_sequence=1, event_type='paused').events[0]
    body.events = [packet().events[0]] + [template.model_copy(update={
        'event_id': uuid4(), 'event_sequence': i,
    }) for i in range(1, 1000)]
    with transaction(event_db): activity.save_activity(event_db, U, body)
    with transaction(event_db): activity.save_activity(event_db, U, body)
    assert event_db.execute('select count(*) as n from student_attempt_events').fetchone()['n'] == 1000


@pytest.mark.parametrize('digits', [0, 1])
def test_batch_float_identity_uses_database_comparison(event_db, digits):
    import math
    event_db.execute(f'set extra_float_digits={digits}')
    body = full_packet().activity
    body.events[0].song_time_sec = 0.00770000000018626
    body.events[0].elapsed_playing_sec = 0.9876543210987654
    with transaction(event_db): activity.save_activity(event_db, U, body)
    with transaction(event_db): activity.save_activity(event_db, U, body)
    for field in ('song_time_sec', 'elapsed_playing_sec'):
        changed = body.model_copy(deep=True)
        setattr(changed.events[0], field, math.nextafter(getattr(changed.events[0], field), math.inf))
        with pytest.raises(HTTPException, match='identity conflict'):
            with transaction(event_db): activity.save_activity(event_db, U, changed)


@pytest.mark.parametrize('value', ['\x00', float('nan'), '\ud800'])
def test_later_invalid_json_cannot_hide_earlier_identity_conflict(event_db, value):
    with transaction(event_db): activity.save_activity(event_db, U, packet())
    body = full_packet().activity
    body.events[0].metadata = {'changed': True}
    body.events[1].metadata = {'unsupported': value}
    with pytest.raises(HTTPException) as error:
        with transaction(event_db): activity.save_activity(event_db, U, body)
    assert error.value.status_code == 409
