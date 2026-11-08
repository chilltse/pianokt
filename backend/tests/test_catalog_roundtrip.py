"""Real PostgreSQL catalog -> activity -> upload -> projections -> export context."""
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException
from test_activity import db, full_packet, U, A
from pianokt_backend import api
from pianokt_backend.activity import save_activity
from pianokt_backend.upload_contracts import Upload, Finalize, validate_finalization
from pianokt_backend.song_import import analyze_midi, publish
from pianokt_backend.projections import project_challenge
from pianokt_backend.pipeline.attempts import read_context, build_record


class ImportConnection:
    def __init__(self,c): self.c=c
    def execute(self,*args): return self.c.execute(*args)
    @contextmanager
    def transaction(self):
        self.c.execute('begin')
        try:
            yield self.c
            self.c.execute('commit')
        except BaseException:
            self.c.execute('rollback')
            raise


def catalog(c):
    item=analyze_midi(Path('scripts/song_catalog/midi/ode-to-joy.mid'))
    args=dict(title='Integration fixture',composer='Test composer',license='test-only',source_url='https://example.invalid/fixture',slug='integration')
    one=publish(ImportConnection(c),item,**args)
    two=publish(ImportConnection(c),item,**args,song_id=UUID(one['song_id']),segment_index=2)
    return one,two


def test_catalog_activity_upload_and_projections_keep_the_same_fragment(db,monkeypatch,tmp_path):
    one,two=catalog(db)
    body=full_packet()
    context=body.activity.attempt
    context.song_source='builtin';context.song_id=one['song_id']
    context.segment_id=UUID(one['segment_id']);context.segment_index=1;context.midi_sha256=one['midi_sha256']
    save_activity(db,U,body.activity)
    @contextmanager
    def connection(): yield db
    monkeypatch.setattr(api,'connect',connection)
    monkeypatch.setattr(api,'signed',lambda *args:'https://example.invalid/upload')
    upload=Upload(schema_version=2,attempt_id=A,song_source='builtin',duration_sec=10,
        song_id=one['song_id'],segment_id=one['segment_id'],segment_index=1,midi_sha256=one['midi_sha256'],
        reference_hash='b'*64,performance_hash='c'*64,settings={})
    api.create_attempt(upload,U)
    row=db.execute('select * from piano_attempts where id=%s',(A,)).fetchone()
    from pianokt_backend.upload_contracts import FinalizeRequest
    wire = body.model_dump(mode='json')
    for key in ('song_id','song_source','segment_id','segment_index','midi_sha256'):
        wire['activity']['attempt'].pop(key, None)
    resolved = FinalizeRequest.model_validate(wire).resolve(row)
    assert resolved.activity.attempt == body.activity.attempt
    validate_finalization(row,resolved,U)
    wrong_row = {**row, 'id': '00000000-0000-0000-0000-000000000000'}
    with pytest.raises(HTTPException, match='does not match attempt'):
        FinalizeRequest.model_validate(wire).resolve(wrong_row)
    validate_finalization(row,body,U)
    changed=body.model_copy(deep=True)
    changed.activity.attempt.segment_id=UUID(two['segment_id']);changed.activity.attempt.segment_index=2
    with pytest.raises(HTTPException,match='original MIDI/segment'):
        validate_finalization(row,changed,U)
    row.update(validated_at=True,status='READY',summary=dict(alignment_quality_valid=True,accuracy=.9))
    project_challenge(db,row,body)
    for table in ('piano_attempts','student_attempts','challenge_recordings','user_play_logs'):
        stored=db.execute(f'select segment_id,segment_index,midi_sha256 from {table}').fetchone()
        assert stored==dict(segment_id=one['segment_id'],segment_index=1,midi_sha256=one['midi_sha256'])
    snapshot=read_context(db,'2100-01-01T00:00:00Z')
    assert snapshot['attempts'][0]['segment_id']==one['segment_id']
    from test_attempt_dataset import fixture
    objects,_,_=fixture(tmp_path)
    import json
    result=json.loads(objects.read('alignment/a1/result.json'))
    result['practice_metadata']=row['metadata']
    record=build_record(result,snapshot['attempts'][0],1,1)
    online=db.execute('select exercise_id from user_play_logs').fetchone()['exercise_id']
    assert record['exercise_id']==online



def test_public_catalog_rpc_hides_drafts_and_clients_cannot_write(db):
    one,two=catalog(db)
    db.execute("update piano_song_segments set status='draft' where segment_id=%s",(two['segment_id'],))
    db.execute('set role anon')
    rows=db.execute('select public.piano_public_catalog() as catalog').fetchone()['catalog']
    assert len(rows)==1 and rows[0]['segment_id']==one['segment_id']
    assert rows[0]['song_id']==one['song_id'] and 'song_version_id' not in rows[0]
    with pytest.raises(RuntimeError,match='permission denied'):
        db.execute("update piano_song_segments set status='published'")



def test_old_activity_does_not_guess_identity_from_current_catalog(db):
    one,_=catalog(db)
    body=full_packet().activity
    # Seed a known legacy activity under its original local identity, then mark
    # it as an official pre-upgrade row with all content fields unknown.
    save_activity(db,U,body)
    db.execute("update student_attempts set song_id=%s,song_source='builtin' where attempt_id=%s",(one['song_id'],A))
    body.attempt.song_source='builtin';body.attempt.song_id=one['song_id']
    body.attempt.segment_id=UUID(one['segment_id']);body.attempt.segment_index=1;body.attempt.midi_sha256=one['midi_sha256']
    # Catalog availability alone is not evidence of which MIDI this old take used.
    with pytest.raises(HTTPException,match='MIDI/segment identity conflict'):
        save_activity(db,U,body)
    assert db.execute('select segment_id from student_attempts where attempt_id=%s',(A,)).fetchone()['segment_id'] is None
