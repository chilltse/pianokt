from contextlib import contextmanager
from io import BytesIO
import json
import mido
import pytest
from test_activity import db, new_upload, full_packet, A, U
from pianokt_backend import worker
from pianokt_backend.outbox_dispatch import recover_pending
from pianokt_backend.contracts import digest
from pianokt_backend.storage import Objects
from pianokt_backend.upload_service import commit_finalization
from pianokt_backend.upload_validation import validate_files, InvalidUpload


def prepare(db, monkeypatch, tmp_path):
    @contextmanager
    def connect():
        db.execute('begin')
        try:
            yield db
            db.execute('commit')
        except Exception:
            db.execute('rollback')
            raise
    monkeypatch.setattr(worker,'connect',connect)
    monkeypatch.setenv('DATA_ROOT',str(tmp_path))
    row=new_upload(db)
    midi=mido.MidiFile(); midi.tracks.append(mido.MidiTrack())
    output=BytesIO(); midi.save(file=output); data=output.getvalue()
    from pianokt_backend.storage_layout import raw_recording_key, raw_reference_key
    store=Objects(str(tmp_path))
    keys=[raw_recording_key(row['learner_key'],A),raw_reference_key(row['learner_key'],A)]
    for key in keys: store.put(key,data)
    db.execute('update piano_attempts set performance_key=%s,reference_key=%s',tuple(keys))
    db.execute('update piano_attempts set performance_hash=%s,reference_hash=%s',(digest(data),digest(data)))
    commit_finalization(db,row,U,full_packet())
    return db.execute('select * from piano_attempts').fetchone()


def test_worker_validates_before_alignment_and_then_publishes(db,monkeypatch,tmp_path):
    prepare(db,monkeypatch,tmp_path)
    assert db.execute('select * from get_leaderboard()').fetchall()==[]
    assert db.execute('select * from user_play_logs').fetchall()==[]
    worker.process(A)
    row=db.execute('select * from piano_attempts').fetchone()
    assert row['status']=='READY' and row['validated_at'] and row['result_committed_at']
    assert len(db.execute('select * from get_leaderboard()').fetchall())==1
    worker.process(A) # terminal duplicate acknowledged
    assert db.execute('select processing_attempts from piano_attempts').fetchone()['processing_attempts']==1


def test_invalid_file_is_terminal_and_excluded(db,monkeypatch,tmp_path):
    prepare(db,monkeypatch,tmp_path)
    (tmp_path/db.execute('select performance_key from piano_attempts').fetchone()['performance_key']).write_bytes(b'wrong')
    worker.process(A)
    row=db.execute('select * from piano_attempts').fetchone()
    assert row['status']=='INVALID' and row['validated_at'] is None
    assert 'identity mismatch' in row['validation_error']
    worker.process(A)
    assert db.execute('select * from get_leaderboard()').fetchall()==[]
    assert db.execute('select * from user_play_logs').fetchall()==[]


def test_missing_file_retries_and_relay_recovers(db,monkeypatch,tmp_path):
    prepare(db,monkeypatch,tmp_path)
    (tmp_path/db.execute('select performance_key from piano_attempts').fetchone()['performance_key']).unlink()
    with pytest.raises(FileNotFoundError): worker.process(A)
    row=db.execute('select * from piano_attempts').fetchone()
    assert row['status']=='RETRY' and row['validated_at'] is None
    db.execute("update piano_outbox set published_at=now()-interval '5 minutes'")
    recover_pending(db)
    assert db.execute('select published_at from piano_outbox').fetchone()['published_at']
    db.execute("update piano_attempts set next_retry_at=now()-interval '1 minute'")
    recover_pending(db)
    assert db.execute('select published_at from piano_outbox').fetchone()['published_at'] is None
    db.execute("update piano_attempts set status='VALIDATING',processing_attempts=8,lease_until=now()-interval '1 minute'")
    recover_pending(db)
    assert db.execute('select status from piano_attempts').fetchone()['status']=='FAILED'


def test_telemetry_monotonic_error_is_permanent(db,monkeypatch,tmp_path):
    row=prepare(db,monkeypatch,tmp_path)
    payload={'schemaVersion':1,'events':[], 'transportSegments':[
      {'sequence':0,'type':'start','playerSongTime':2,'performanceNowTimestamp':1},
      {'sequence':1,'type':'stop','playerSongTime':1,'performanceNowTimestamp':2}]}
    from pianokt_backend.storage_layout import raw_telemetry_key
    key=raw_telemetry_key(row['learner_key'],A)
    data=json.dumps(payload).encode(); Objects(str(tmp_path)).put(key,data)
    db.execute('update piano_attempts set performance_events_key=%s,performance_events_hash=%s,performance_events_count=0',(key,digest(data)))
    worker.process(A)
    row=db.execute('select * from piano_attempts').fetchone()
    assert row['status']=='INVALID' and 'not monotonic' in row['validation_error']


def test_generation_is_pinned_and_size_checked_before_download(tmp_path):
    from types import SimpleNamespace
    from pianokt_backend.upload_validation import read_verified
    calls=[]
    class Blob:
        size=3
        generation=7
        def reload(self,**kwargs): calls.append('reload')
        def download_as_bytes(self,**kwargs):
            assert kwargs['if_generation_match']==7
            calls.append('download')
            return b'abc'
    blob=Blob()
    def make_blob(key,**kwargs):
        if kwargs: assert kwargs=={'generation':7}
        return blob
    store=SimpleNamespace(bucket=SimpleNamespace(blob=make_blob),_name=lambda key:key)
    row={'performance_key':'p','performance_hash':digest(b'abc'),'file_generations':{'performance':'7'}}
    generations={}
    assert read_verified(store,row,'performance',3,generations)==b'abc'
    assert generations=={'performance':'7'}
    calls.clear();blob.size=4
    with pytest.raises(InvalidUpload): read_verified(store,row,'performance',3,{})
    assert calls==['reload']


def test_reference_contract_failure_is_terminal_internal_error(db, monkeypatch, tmp_path):
    prepare(db, monkeypatch, tmp_path)
    def fail(*args, **kwargs):
        raise worker.ReferenceGroupingMismatch('group=0 missing ref_id=r0_n0')
    monkeypatch.setattr(worker, 'align', fail)
    worker.process(A)
    row = db.execute('select * from piano_attempts').fetchone()
    assert row['status'] == 'FAILED'
    assert row['error_code'] == 'ReferenceGroupingMismatch'
    assert row['validation_error'] is None
    assert row['result_key'] is None
    assert not list(tmp_path.glob('silver/alignment/*/result.json'))
