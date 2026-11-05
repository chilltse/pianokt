import json
import math
import os
import uuid
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from psycopg.types.json import Jsonb
from .online import connect
from .storage import Objects
from .contracts import digest,AlignmentConfig
from .alignment.service import align,alignment_run_id,matcher_version


def _finite_nonnegative(value,default=None):
    if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0:
        return float(value)
    return default


def _performance_event_sidecar(row,store):
    """Load the immutable browser-side audit that was verified at finalize.

    The worker checks the digest again because it is the boundary that turns the
    uploaded bundle into an alignment result.  Historical attempts have no
    sidecar and deliberately follow the bounded legacy path in ``align``.
    """
    key=row.get('performance_events_key')
    expected_hash=row.get('performance_events_hash')
    if not key and not expected_hash:
        return None,[],None
    if not key or not expected_hash:
        raise ValueError('Incomplete performance event sidecar identity')
    data=store.read(key)
    if digest(data)!=expected_hash:
        raise ValueError('Performance event telemetry integrity mismatch')
    try:
        payload=json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError('Invalid performance event telemetry JSON') from exc
    if not isinstance(payload,dict) or payload.get('schemaVersion')!=1:
        raise ValueError('Invalid performance event telemetry schema')
    events=payload.get('events')
    segments=payload.get('transportSegments')
    if not isinstance(events,list) or not isinstance(segments,list):
        raise ValueError('Invalid performance event telemetry payload')
    if row.get('performance_events_count') is not None and len(events)!=row['performance_events_count']:
        raise ValueError('Performance event telemetry count mismatch')
    return events,segments,payload


def _alignment_window(settings):
    """Return snapshot-relative observed end and the absolute snapshot origin."""
    settings=settings if isinstance(settings,dict) else {}
    observed_payload=settings.get('observed_range',settings.get('range'))
    if observed_payload is not None and not isinstance(observed_payload,dict):
        raise ValueError('Observed range must be an object')
    observed=observed_payload or {}
    has_start='start' in observed
    has_end='end' in observed
    observed_start=_finite_nonnegative(observed.get('start')) if has_start else None
    observed_end=_finite_nonnegative(observed.get('end')) if has_end else None
    if (has_start and observed_start is None) or (has_end and observed_end is None):
        raise ValueError('Observed range must use finite non-negative seconds')
    if observed_start is not None and observed_end is not None and observed_end < observed_start:
        raise ValueError('Observed range end precedes start')

    origin_value=settings.get('reference_time_origin_sec')
    origin=_finite_nonnegative(origin_value)
    if origin_value is not None and origin is None:
        raise ValueError('Reference time origin must be finite and non-negative')
    if origin is None:
        origin=observed_start if observed_start is not None else 0.0
    if observed_start is not None and abs(origin-observed_start)>1e-6:
        raise ValueError('Reference time origin must equal observed range start')

    end=observed_end
    if end is None and 'played_until_sec' in settings:
        end=_finite_nonnegative(settings.get('played_until_sec'))
        if end is None:
            raise ValueError('Played-until time must be finite and non-negative')
    if end is not None and end < origin:
        raise ValueError('Observed range end precedes reference time origin')
    relative_end=end-origin if end is not None else None
    return relative_end,origin


def _quality(result):
    diagnostics=result.get('diagnostics') if isinstance(result.get('diagnostics'),dict) else {}
    summary=result.get('summary') if isinstance(result.get('summary'),dict) else {}
    valid=diagnostics.get('alignment_quality_valid',summary.get('alignment_quality_valid',False))
    reason=diagnostics.get('alignment_quality_reason',summary.get('alignment_quality_reason'))
    return valid is True,str(reason or ('ok' if valid is True else 'alignment_quality_not_valid'))


def _read_json_or_none(store,key):
    try:
        return json.loads(store.read(key))
    except FileNotFoundError:
        return None
    except Exception as exc:
        # Local test storage raises FileNotFoundError; GCS uses this exception.
        from google.api_core.exceptions import NotFound
        if isinstance(exc,NotFound):
            return None
        raise


def _is_current_result(result,row,config,expected_run,observed_end_sec,reference_origin_sec,performance_events,transport_segments):
    """Only reuse an immutable result made by this matcher contract.

    Result keys are derived from the complete matcher identity, including raw
    MIDI hashes and time telemetry. A retry therefore reads that deterministic
    result directly; old mutable/legacy attempt manifests are never consulted.
    """
    if not isinstance(result,dict):
        return False
    context=result.get('alignment_context')
    if not isinstance(context,dict):
        return False
    return (
        result.get('attempt_id')==str(row['id'])
        and result.get('alignment_run_id')==expected_run
        and result.get('score_version')==row['reference_hash']
        and result.get('performance_hash')==row['performance_hash']
        and result.get('matcher_version')==matcher_version()
        and result.get('config')==config.identity
        and result.get('source_environment','unknown')==row.get('source_environment','unknown')
        and context.get('reference_origin_sec')==reference_origin_sec
        and context.get('observed_end_sec')==observed_end_sec
        and context.get('transport_segments_count')==(
            len(transport_segments) if isinstance(transport_segments,list) else None
        )
        and context.get('event_audit_supplied')==(performance_events is not None)
    )

def commit_alignment(c,attempt_id,lease_token,result_key,result):
    """Publish READY, authoritative score and outbox in the caller transaction."""
    current=c.execute('select lease_token from public.piano_attempts where id=%s for update',(attempt_id,)).fetchone()
    if str(current['lease_token'])!=lease_token:
        raise RuntimeError('Worker lease superseded')
    c.execute("update public.piano_attempts set status='READY',result_key=%s,summary=%s,alignment_run_id=%s,error_code=null,lease_until=null,updated_at=now() where id=%s",(result_key,Jsonb(result['summary']),result['alignment_run_id'],attempt_id))
    c.execute('update public.piano_attempts set result_digest=%s,result_committed_at=clock_timestamp() where id=%s',
              (digest(json.dumps(result,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()),attempt_id))
    quality_valid,_=_quality(result)
    accuracy=result['summary'].get('accuracy')
    c.execute('update public.challenge_recordings set accuracy_pct=%s,score_source=%s where id=%s',
              (float(accuracy)*100 if quality_valid and accuracy is not None else 0,'aligned' if quality_valid and accuracy is not None else 'rejected',attempt_id))
    payload=dict(event_type='alignment.completed',attempt_id=attempt_id,alignment_run_id=result['alignment_run_id'],result_key=result_key,source_environment=result.get('source_environment','unknown'))
    c.execute('insert into public.piano_outbox(event_type,aggregate_id,payload) values(%s,%s,%s) on conflict do nothing',('alignment.completed',result['alignment_run_id'],Jsonb(payload)))

def process(attempt_id):
    attempt_id=str(uuid.UUID(str(attempt_id)))
    lease_token=str(uuid.uuid4())
    with connect() as c:
        row=c.execute("""update public.piano_attempts set status='PROCESSING',lease_token=%s,lease_until=now()+interval '10 minutes',updated_at=now()
            where id=%s and (status in ('UPLOADED','FAILED') or (status='PROCESSING' and lease_until<now())) returning *""",(lease_token,attempt_id,)).fetchone()
        if not row:
            current=c.execute('select status from public.piano_attempts where id=%s',(attempt_id,)).fetchone()
            if current and current['status']=='READY': return
            raise RuntimeError('Attempt busy or not finalized')
    store=Objects(os.environ['RAW_ROOT'])
    try:
        settings=row['metadata'].get('settings',{}) if isinstance(row.get('metadata'),dict) else {}
        observed_end_sec,reference_origin_sec=_alignment_window(settings)
        performance_events,transport_segments,telemetry=_performance_event_sidecar(row,store)
        config=AlignmentConfig()
        # The result key itself is the immutable crash-recovery marker. It is
        # impossible for a legacy Parangonar manifest to pin this matcher to a
        # stale result, and no create-only manifest needs to be overwritten.
        with TemporaryDirectory(prefix='pianokt-worker-') as tmp:
            files={}
            for kind in ('performance','reference'):
                data=store.read(row[kind+'_key'])
                if digest(data)!=row[kind+'_hash']: raise ValueError('MIDI integrity mismatch')
                files[kind]=Path(tmp)/(kind+'.mid'); files[kind].write_bytes(data)
            run=alignment_run_id(
                files['reference'],files['performance'],attempt_id,config,
                performance_events,observed_end_sec,transport_segments,reference_origin_sec,
            )
            result_key=f'alignment/{run}/result.json'
            result=_read_json_or_none(store,result_key)
            if result is not None and not _is_current_result(
                result,row,config,run,observed_end_sec,reference_origin_sec,
                performance_events,transport_segments,
            ):
                raise ValueError('Immutable alignment result identity conflict')
            if result is None:
                result=align(
                    files['reference'],files['performance'],attempt_id,config,
                    stage_sink=lambda run,data:store.json(f'matching/{run}/matches.json',data),
                    performance_events=performance_events,
                    observed_end_sec=observed_end_sec,
                    transport_segments=transport_segments,
                    reference_origin_sec=reference_origin_sec,
                )
                if result['alignment_run_id']!=run:
                    raise ValueError('Alignment run identity mismatch')
                result.update(
                learner_key=str(row['learner_key']),
                song_id=row['song_id'],
                session_id=str(row['id']),
                source_environment=row.get('source_environment','unknown'),
                practice_metadata=row['metadata'],
                attempt_created_at=row['created_at'].isoformat(),
                alignment_completed_at=datetime.now(timezone.utc).isoformat(),
                performance_events=performance_events or [],
                transport_segments=transport_segments,
                performance_events_key=row.get('performance_events_key'),
                performance_events_hash=row.get('performance_events_hash'),
                performance_event_audit_schema_version=(telemetry or {}).get('schemaVersion'),
            )
                quality_valid,quality_reason=_quality(result)
                for note in result['notes']:
                    note['within_observed_range']=observed_end_sec is None or note.get('ref_onset_sec') is None or note['ref_onset_sec']<=observed_end_sec
                    note['timing_labels_valid']=not settings.get('waiting',False) and quality_valid
                    note['alignment_quality_valid']=quality_valid
                    note['alignment_quality_reason']=quality_reason
                observed=[n for n in result['notes'] if n['within_observed_range']]
                expected=sum(n['alignment_type'] in ('match','deletion') for n in observed)
                correct=sum(n['is_correct'] for n in observed)
                result['summary'].update(
                    expected_notes=expected,
                    correct_notes=correct,
                    accuracy=correct/expected if expected and quality_valid else None,
                    missing_notes=sum(n['alignment_type']=='deletion' for n in observed),
                    timing_labels_valid=not settings.get('waiting',False) and quality_valid,
                    alignment_quality_valid=quality_valid,
                    alignment_quality_reason=quality_reason,
                )
                result['alignment_quality_valid']=quality_valid
                result['alignment_quality_reason']=quality_reason
                try:
                    store.json(result_key,result)
                except ValueError:
                    # A duplicate lease may have completed first. Use the
                    # identical immutable result only if it satisfies this
                    # exact run identity; never overwrite it.
                    result=_read_json_or_none(store,result_key)
                    if result is None or not _is_current_result(
                        result,row,config,run,observed_end_sec,reference_origin_sec,
                        performance_events,transport_segments,
                    ):
                        raise
        with connect() as c:
            commit_alignment(c,attempt_id,lease_token,result_key,result)
    except Exception as exc:
        with connect() as c: c.execute("update public.piano_attempts set status='FAILED',error_code=%s,lease_until=null,updated_at=now() where id=%s and lease_token=%s and status<>'READY'",(type(exc).__name__,attempt_id,lease_token))
        raise

def relay(limit=100):
    from google.cloud import pubsub_v1
    publisher=pubsub_v1.PublisherClient(); count=0
    for _ in range(limit):
        with connect() as c:
            row=c.execute('select * from public.piano_outbox where published_at is null order by created_at for update skip locked limit 1').fetchone()
            if not row: break
            data=dict(row['payload'],event_id=str(row['event_id']),event_type=row['event_type'])
            publisher.publish(os.environ['EVENT_TOPIC'],json.dumps(data).encode(),event_type=row['event_type']).result(timeout=30)
            c.execute('update public.piano_outbox set published_at=now() where event_id=%s',(row['event_id'],)); count+=1
    return count
