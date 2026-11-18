"""Committed Outbox delivery shared by the API fast path and compensation Job.

At-least-once delivery: an ACK without a committed published_at may be replayed.
No alignment engine or cloud client is loaded until it is actually needed.
"""
from contextlib import ExitStack
import json
import logging
import math
import os
from threading import Lock
from time import monotonic
from uuid import UUID

from .online import connect
from .timing import stage, sql, trace_id

_publisher = None
_publisher_lock = Lock()


def publisher():
    global _publisher
    with _publisher_lock:
        if _publisher is None:
            from google.cloud import pubsub_v1
            _publisher = pubsub_v1.PublisherClient()
        return _publisher


def close_publisher():
    global _publisher
    with _publisher_lock:
        client, _publisher = _publisher, None
    if client is not None:
        # stop() is nonblocking. Closing transport may cancel an unacknowledged
        # publish; its durable Outbox row remains available for compensation.
        client.stop()
        client.transport.close()


def log_failure(event_id, phase, exc):
    logging.getLogger('uvicorn.error').warning(
        'request_id=%s event_id=%s stage=%s outcome=error error_type=%s',
        trace_id.get(), event_id, phase, type(exc).__name__)


def _positive_timeout(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Dispatch timeout must be finite and positive')
    return value


def direct_publish_enabled():
    return os.getenv('DIRECT_PUBSUB_ENABLED', 'false').lower() in ('true', '1')


def dispatch_after_commit(event_id):
    """Best effort only AFTER durable acceptance; never turns it into an error."""
    if event_id is None or not direct_publish_enabled():
        return
    try:
        timeout = _positive_timeout(os.getenv('DIRECT_PUBSUB_TIMEOUT_S', '2'))
        publish_outbox_event(event_id, timeout_s=timeout)
    except Exception as exc:
        # Contain both transport/DB failures and configuration failures after
        # COMMIT. Do not catch process termination; Outbox covers that window.
        log_failure(event_id, 'outbox.direct', exc)


def _publish_one(event_id=None, *, timeout_s, submitted_only=False):
    timeout_s = _positive_timeout(timeout_s)
    phase = 'outbox.select'
    selected_id = event_id
    try:
        # The outer timer stack keeps outbox.confirm open through DB COMMIT.
        with stage('outbox.transaction'), ExitStack() as timings:
            with connect() as c:
                if event_id is None:
                    row = sql(c, 'outbox.claim', '''select * from public.piano_outbox
                        where published_at is null order by created_at,event_id
                        for update skip locked limit 1''').fetchone()
                else:
                    row = sql(c, 'outbox.claim', '''select * from public.piano_outbox
                        where event_id=%s and published_at is null
                        and (%s=false or event_type='performance.submitted')
                        for update skip locked''', (event_id, submitted_only)).fetchone()
                if row is None:
                    logging.getLogger('uvicorn.error').info(
                        'request_id=%s event_id=%s stage=outbox.dispatch outcome=skipped', trace_id.get(), event_id)
                    return False  # Already published, absent, or another publisher owns it.
                selected_id = str(row['event_id'])
                data = dict(row['payload'], event_id=selected_id, event_type=row['event_type'])
                phase = 'pubsub.publish'
                with stage('pubsub.publish'):
                    client = publisher()
                    deadline = monotonic() + timeout_s
                    future = client.publish(os.environ['EVENT_TOPIC'], json.dumps(data).encode(),
                                            event_type=row['event_type'], retry=None, timeout=timeout_s)
                    # A timed-out future can still deliver; never mark it as ACKed.
                    future.result(timeout=max(0, deadline - monotonic()))
                phase = 'outbox.confirm'
                timings.enter_context(stage('outbox.confirm'))
                sql(c, 'outbox.confirm', 'update public.piano_outbox set published_at=now() where event_id=%s',
                    (row['event_id'],))
        logging.getLogger('uvicorn.error').info(
            'request_id=%s event_id=%s stage=outbox.dispatch outcome=confirmed', trace_id.get(), selected_id)
        return True
    except Exception as exc:
        log_failure(selected_id, phase, exc)
        raise


def publish_outbox_event(event_id, *, timeout_s=2.0):
    """Re-read a committed submitted event; never rebuild a frontend payload."""
    event_id = str(UUID(str(event_id)))
    return _publish_one(event_id, timeout_s=timeout_s, submitted_only=True)


def recover_pending(c):
    c.execute("""update public.piano_attempts set status='FAILED',error_code='RetryExhausted',
        lease_until=null,updated_at=now() where processing_attempts>=8 and
        ((status in ('VALIDATING','PROCESSING') and lease_until<now()) or
         (status='RETRY' and next_retry_at<=now()))""")
    # Re-arm published outbox rows after transient failure, expired leases, or
    # lost/dead-lettered notifications. Worker claiming remains the authority.
    c.execute("""update public.piano_outbox o set published_at=null
      from public.piano_attempts a where o.aggregate_id=a.id::text
      and o.event_type in ('performance.submitted','performance.uploaded')
      and o.published_at < now()-interval '2 minutes' and (
        (a.status in ('ACCEPTED','UPLOADED') and a.updated_at<now()-interval '2 minutes') or
        (a.status='RETRY' and a.next_retry_at<=now()) or
        (a.status in ('VALIDATING','PROCESSING') and a.lease_until<now()))""")


def relay(limit=100, *, max_duration_s=600):
    """Bound the batch by count and elapsed budget; one short transaction/event."""
    deadline = monotonic() + _positive_timeout(max_duration_s)
    count = 0
    with stage('outbox.recover'), connect() as c:
        recover_pending(c)
    for _ in range(limit):
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        if not _publish_one(timeout_s=min(30, remaining)):
            break
        count += 1
    logging.getLogger('uvicorn.error').info('stage=outbox.relay published_count=%d', count)
    return count
