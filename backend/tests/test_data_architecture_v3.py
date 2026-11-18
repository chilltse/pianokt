"""V3 full MIDI processing, commit authority, research isolation and namespace tests."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import copy
import json
import sys
import uuid
import pytest
from test_activity import db, U, A, full_packet
from test_time_guided_alignment import write_midi, sidecar
from pianokt_backend import api, worker
from pianokt_backend.contracts import digest, AlignmentConfig
from pianokt_backend.storage import Objects
from pianokt_backend.storage_layout import *
from pianokt_backend.publication import read_bundle
from pianokt_backend.pipeline.direct import read_committed
from pianokt_backend.pipeline.attempts import read_context, export_attempts
from pianokt_backend.pipeline.training import export_dataset
from pianokt_backend.run_registry import register_run


@contextmanager
def transaction(db):
    db.execute('begin')
    try:
        yield db
        db.execute('commit')
    except BaseException:
        db.execute('rollback')
        raise


def completed(db, monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'connect', lambda: transaction(db))
    monkeypatch.setattr(worker, 'connect', lambda: transaction(db))
    monkeypatch.setattr(api, 'signed', lambda key, *args: 'https://fixture.invalid/' + key)
    monkeypatch.setattr(api, 'dispatch_after_commit', lambda *a: None)
    monkeypatch.setenv('DATA_ROOT', str(tmp_path/'objects'))
    store = Objects(str(tmp_path/'objects'))
    notes = [(60,0,.2),(62,.5,.2),(64,1,.2),(65,1.5,.2)]
    reference=tmp_path/'reference.mid'; performance=tmp_path/'performance.mid'
    write_midi(reference, notes); write_midi(performance, notes)
    telemetry=sidecar(notes)
    event_bytes=json.dumps(telemetry,sort_keys=True,separators=(',',':')).encode()
    upload=api.Upload(schema_version=2,attempt_id=A,song_id='song',song_source='local',duration_sec=2,
        performance_hash=digest(performance.read_bytes()),reference_hash=digest(reference.read_bytes()),
        performance_events_hash=digest(event_bytes),performance_events_count=len(telemetry['events']),
        settings={'frontend_note_accuracy':.75,'observed_range':{'start':0,'end':2}})
    api.create_attempt(upload,U)
    api.create_attempt(upload,U)  # Duplicate request preserves immutable identity.
    row=db.execute('select * from piano_attempts').fetchone()
    for key, data in [(row['reference_key'], reference.read_bytes()),(row['performance_key'],performance.read_bytes()),(row['performance_events_key'],event_bytes)]:
        store.put(key,data); store.put(key,data)
    body=full_packet().model_dump(mode='json')
    body['activity']['attempt']={k:v for k,v in body['activity']['attempt'].items() if k in ('attempt_id','visit_attempt_sequence','started_at')}
    api.finalize(uuid.UUID(A),api.FinalizeRequest.model_validate(body),U)
    worker.process(A); worker.process(A)
    row=db.execute('select * from piano_attempts').fetchone()
    assert row['status']=='READY' and row['data_architecture_version']=='3'
    assert row['processing_attempts']==1
    result=json.loads(store.read(row['result_key']))
    assert result['frontend_note_accuracy']==.75
    assert result['backend_event_accuracy']==1
    assert db.execute('select count(*) n from piano_alignment_runs').fetchone()['n']==1
    return store,row,result


def test_real_midi_upload_commit_both_gold_and_research_isolation(db,monkeypatch,tmp_path):
    store,row,result=completed(db,monkeypatch,tmp_path)
    cutoff=datetime.now(timezone.utc)+timedelta(seconds=1)
    with transaction(db):
        source=read_committed(db,cutoff)
        context=read_context(db,cutoff)
    for kind,export in [('attempts',export_attempts),('knowledge_tracing',export_dataset)]:
        manifest=export(store,source,cutoff,None,context)
        prefix=gold_dataset_prefix(kind,manifest['dataset_version'])
        verified, files=read_bundle(store,prefix)
        assert verified['attempt_count']==1 and verified['event_count']==4
        assert verified['source_runs'][0]['alignment_run_id']==result['alignment_run_id']
        assert export(store,source,cutoff,None,context)==manifest
    # Research variant uses the identical computational path and stays off-line.
    from pianokt_backend.upload_validation import validate_files
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as directory:
        files,telemetry,_=validate_files(store,row,directory)
        key,research=worker.build_silver(store,row,files,telemetry,AlignmentConfig(match_window_sec=.9))
    with transaction(db): register_run(db,row,key,research,'research')
    with transaction(db): register_run(db,row,key,research,'research')
    assert db.execute('select alignment_run_id from piano_attempts').fetchone()['alignment_run_id']==result['alignment_run_id']
    assert len(read_committed(db,cutoff).rows)==1
    with pytest.raises(ValueError,match='Multiple eligible'):
        read_committed(db,cutoff,alignment_version='1.0.0')
    chosen=read_committed(db,cutoff,run_selection={A:research['alignment_run_id']})
    assert len(chosen.rows)==1
    with pytest.raises(RuntimeError,match='immutable'):
        db.execute('update piano_alignment_runs set quality_valid=false')
    # Exclusions persist outside facts and block both fresh exports and reprocessing.
    db.execute("insert into piano_data_exclusions values('production','attempt',%s,'withdrawn',now())",(A,))
    assert read_committed(db,cutoff).rows==()
    assert read_context(db,cutoff)['attempts']==[]
    from pianokt_backend import reprocess
    monkeypatch.setattr(reprocess,'connect',lambda:transaction(db))
    report=reprocess.reprocess(store,[A],'production')
    assert len(report['failed'])==1 and 'excluded' in report['failed'][0]['reason']


def test_commit_conflict_lease_expiry_and_legacy_exclusion(db,monkeypatch,tmp_path):
    store,row,result=completed(db,monkeypatch,tmp_path)
    changed=copy.deepcopy(result);changed['diagnostics']['tamper']=True
    with pytest.raises(ValueError,match='Immutable run identity conflict'):
        with transaction(db): register_run(db,row,row['result_key'],changed,'online')
    with pytest.raises(RuntimeError,match='superseded'):
        with transaction(db): worker.commit_alignment(db,A,str(row['lease_token']),row['result_key'],result)
    # A file is not a commit: research output with no registry insert is invisible.
    before=len(read_committed(db,datetime.now(timezone.utc)+timedelta(seconds=1)).rows)
    store.json(silver_result_key('orphan'),{**result,'alignment_run_id':'orphan'})
    assert len(read_committed(db,datetime.now(timezone.utc)+timedelta(seconds=1)).rows)==before
    db.execute("update piano_attempts set data_architecture_version='legacy'")
    assert read_committed(db,datetime.now(timezone.utc)+timedelta(seconds=1)).rows==()


def test_namespace_and_no_lake_runtime():
    assert raw_reference_key('learner','attempt')=='raw/recordings/learner/attempt/reference.mid'
    with pytest.raises(ValueError): raw_recording_key('../other','attempt')
    with pytest.raises(ValueError): gold_dataset_prefix('attempts','v','datasets/old')
    # Pandas can optionally import an installed Arrow. Simulate an ordinary
    # deployment where the removed dependencies really are unavailable.
    import subprocess
    script = '''
import sys, importlib.abc
class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('deltalake','pyspark','delta','pyarrow'):
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, Absent())
from pianokt_backend import cli, worker
from pianokt_backend.pipeline import direct, attempts, training
'''
    subprocess.run([sys.executable, '-c', script], check=True)
