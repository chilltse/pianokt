"""Regression tests for the UUID song / segment / original-content contract."""
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pianokt_backend.activity import AttemptContext
from pianokt_backend.upload_contracts import Upload, validate_finalization
from pianokt_backend.song_identity import check_catalog_identity, exercise_identity, assert_export_identity
from pianokt_backend.attempt_context import attempt_context, attach_attempt_context

SONG='70000000-0000-0000-0000-000000000001'
SEGMENT='80000000-0000-0000-0000-000000000001'
HASH='a'*64
IDENTITY=dict(song_id=SONG,segment_id=SEGMENT,segment_index=1,midi_sha256=HASH)


def upload(**changes):
    return Upload.model_validate(dict(schema_version=2,attempt_id=SONG,
        performance_hash='b'*64,reference_hash='c'*64,duration_sec=1,**{'song_source':'builtin',**IDENTITY,**changes}))


@pytest.mark.parametrize('changes', [dict(song_id='canon.mid'),dict(segment_id=None),dict(segment_index=None),dict(midi_sha256=None),dict(segment_index=0)])
def test_official_upload_rejects_old_or_partial_identity(changes):
    with pytest.raises(ValidationError): upload(**changes)


def test_local_upload_keeps_file_handle_identity_without_official_fields():
    value=Upload.model_validate(dict(
        schema_version=2,attempt_id=SONG,song_source='local',song_id='my-file.mid',
        performance_hash='b'*64,reference_hash='c'*64,duration_sec=1))
    assert value.segment_id is None and value.song_id=='my-file.mid'
    with pytest.raises(ValidationError): upload(song_source='local')


def test_activity_and_upload_share_the_same_identity_contract():
    context=AttemptContext(attempt_id=SONG,visit_attempt_sequence=1,song_source='builtin',
        started_at='2026-01-01T00:00:00Z',**IDENTITY)
    assert str(context.segment_id)==str(upload().segment_id)
    with pytest.raises(ValidationError):
        AttemptContext(**{**context.model_dump(), 'song_version_id':SONG})
    with pytest.raises(ValidationError):
        AttemptContext(**{**context.model_dump(), 'segment_id':None})


def test_catalog_rejects_valid_uuids_that_do_not_belong_together():
    calls=[]
    def execute(query,params):
        calls.append(params)
        return SimpleNamespace(fetchone=lambda:None)
    with pytest.raises(HTTPException,match='does not match'):
        check_catalog_identity(SimpleNamespace(execute=execute), upload())
    assert calls[0]==(UUID(SONG),UUID(SEGMENT),1,HASH)


def test_finalization_cannot_switch_segment_or_original_hash():
    from test_activity import full_packet, A, U
    body=full_packet()
    # This test isolates immutable finalization identity from catalog lookups.
    body.activity.attempt.segment_id=UUID(SEGMENT)
    body.activity.attempt.segment_index=1
    body.activity.attempt.midi_sha256=HASH
    row=dict(id=A,song_id='song',song_source='local',metadata=dict(segment_id=SEGMENT,segment_index=1,midi_sha256=HASH))
    validate_finalization(row,body,U)
    row['metadata']['midi_sha256']='d'*64
    with pytest.raises(HTTPException,match='original MIDI/segment'):
        validate_finalization(row,body,U)


def test_projection_writes_content_columns_and_uses_shared_exercise_id():
    from pianokt_backend.projections import project_challenge
    from test_activity import full_packet, A, U
    body=full_packet(); calls=[]
    def execute(query,params):
        assert query.count('%s')==len(params)
        calls.append((query,params))
        return SimpleNamespace(fetchone=lambda:None)
    settings=dict(assigned_range={'start':0,'end':1},left=True,right=False,transpose=0,waiting=False,tracks={'0':{'hand':'left'}})
    row=dict(id=A,user_id=U,song_source='builtin',song_id=SONG,status='READY',validated_at=True,
        summary=dict(alignment_quality_valid=True,accuracy=.95),source_environment='production',
        metadata=dict(duration_sec=1,settings=settings,**IDENTITY))
    project_challenge(SimpleNamespace(execute=execute),row,body)
    recording=next(params for query,params in calls if 'insert into public.challenge_recordings' in query)
    session=next(params for query,params in calls if 'insert into public.user_play_logs' in query)
    assert recording[-3:]==(SEGMENT,1,HASH) and session[-3:]==(SEGMENT,1,HASH)
    assert session[3]==exercise_identity('builtin',SONG,SEGMENT,HASH,settings)
    assert session[3]!=exercise_identity('builtin',SONG,SEGMENT,'d'*64,settings)


def test_debug_replay_preserves_catalog_identity():
    context=attempt_context(dict(id=SONG,song_id=SONG,metadata=IDENTITY))
    result=attach_attempt_context({'events_data':{'onset_seconds':[0]}},context)
    for field,value in IDENTITY.items(): assert result[field]==value


def test_export_refuses_wrong_fragment_and_original_hash():
    context=dict(song_source='builtin',**IDENTITY)
    assert_export_identity(IDENTITY,context)
    for field,value in [('segment_id',SONG),('segment_index',2),('midi_sha256','b'*64)]:
        with pytest.raises(ValueError,match='identity differs'):
            assert_export_identity({**IDENTITY,field:value},context)


def test_reference_snapshot_hash_is_not_the_original_midi_identity():
    value=upload()
    assert value.reference_hash != value.midi_sha256
    context=dict(song_source='builtin',**IDENTITY)
    result={**IDENTITY,'score_version':'independent-reference-snapshot'}
    assert_export_identity(result,context)
    result['midi_sha256']=result['score_version']
    with pytest.raises(ValueError,match='identity differs'):
        assert_export_identity(result,context)
