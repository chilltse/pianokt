from fastapi.testclient import TestClient
from pianokt_backend.api import app,user
from fastapi import HTTPException
import pytest

def test_health_and_auth_required():
    c=TestClient(app)
    assert c.get('/health').status_code==200
    assert c.post('/recommendations').status_code==422
    assert c.post('/recommendations',headers={'Authorization':'invalid'}).status_code==401

def test_payload_validation():
    app.dependency_overrides[user]=lambda:'00000000-0000-0000-0000-000000000001'
    try:
        assert TestClient(app).post('/attempts',json={'attempt_id':'invalid'}).status_code==422
    finally: app.dependency_overrides.clear()

def test_existing_whitelist_is_enforced(monkeypatch):
    from types import SimpleNamespace
    import httpx
    import pytest
    from fastapi import HTTPException
    monkeypatch.setenv('SUPABASE_URL','https://example.invalid')
    monkeypatch.setenv('SUPABASE_ANON_KEY','public-key')
    monkeypatch.delenv('PIANOKT_LOCAL_DEV_BYPASS',raising=False)
    monkeypatch.delenv('PIANOKT_ENV',raising=False)
    uid='00000000-0000-0000-0000-000000000001'
    monkeypatch.setattr(httpx,'get',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:{'id':uid}))
    monkeypatch.setattr(httpx,'post',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:False))
    with pytest.raises(HTTPException) as exc: user('Bearer fixture')
    assert exc.value.status_code==403
    monkeypatch.setattr(httpx,'post',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:True))
    assert user('Bearer fixture')==uid

def test_local_development_can_bypass_only_the_access_list(monkeypatch):
    from types import SimpleNamespace
    import httpx
    monkeypatch.setenv('SUPABASE_URL','https://example.invalid')
    monkeypatch.setenv('SUPABASE_ANON_KEY','public-key')
    monkeypatch.setenv('PIANOKT_ENV','development')
    monkeypatch.setenv('PIANOKT_LOCAL_DEV_BYPASS','1')
    monkeypatch.delenv('K_SERVICE',raising=False)
    uid='00000000-0000-0000-0000-000000000001'
    monkeypatch.setattr(httpx,'get',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:{'id':uid}))
    monkeypatch.setattr(httpx,'post',lambda *a,**kw:pytest.fail('allow-list RPC must not run locally'))
    assert user('Bearer fixture')==uid

def test_cloud_run_cannot_enable_the_local_access_list_bypass(monkeypatch):
    from types import SimpleNamespace
    import httpx
    monkeypatch.setenv('SUPABASE_URL','https://example.invalid')
    monkeypatch.setenv('SUPABASE_ANON_KEY','public-key')
    monkeypatch.setenv('PIANOKT_ENV','development')
    monkeypatch.setenv('PIANOKT_LOCAL_DEV_BYPASS','1')
    monkeypatch.setenv('K_SERVICE','pianokt-api')
    uid='00000000-0000-0000-0000-000000000001'
    monkeypatch.setattr(httpx,'get',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:{'id':uid}))
    monkeypatch.setattr(httpx,'post',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:False))
    with pytest.raises(HTTPException) as exc: user('Bearer fixture')
    assert exc.value.status_code==403

def test_performance_event_telemetry_validation():
    from pianokt_backend.api import validate_performance_events
    payload={
        'schemaVersion':1,
        'events':[
            dict(sequence=0,type='note_on',midiEventTimestamp=100.0,playerSongTime=0.25,performanceNowTimestamp=100.1,pitch=60,velocity=90,timestampSource='web_midi'),
            dict(sequence=1,type='note_off',midiEventTimestamp=300.0,playerSongTime=0.45,performanceNowTimestamp=300.1,pitch=60,velocity=0,timestampSource='web_midi'),
        ],
        'transportSegments':[
            dict(sequence=0,type='start',playerSongTime=0.0,performanceNowTimestamp=0.0),
            dict(sequence=1,type='stop',playerSongTime=0.5,performanceNowTimestamp=500.0),
        ],
    }
    assert validate_performance_events(payload,2)==payload
    payload['events'][1]['sequence']=0
    with pytest.raises(HTTPException,match='sequence'):
        validate_performance_events(payload,2)

def test_performance_event_telemetry_requires_a_complete_transport_cycle():
    from pianokt_backend.api import validate_performance_events
    payload={
        'schemaVersion':1,
        'events':[],
        'transportSegments':[
            dict(sequence=0,type='start',playerSongTime=10.0,performanceNowTimestamp=100.0),
        ],
    }
    with pytest.raises(HTTPException,match='start and end'):
        validate_performance_events(payload,0)

def test_alignment_window_settings_rejects_an_end_before_origin():
    from pianokt_backend.api import validate_alignment_window_settings
    with pytest.raises(HTTPException,match='Invalid observed_range'):
        validate_alignment_window_settings({
            'observed_range':{'start':10.0,'end':5.0},
            'reference_time_origin_sec':10.0,
        })


def test_cycle_timezone_and_pause_state():
    from pianokt_backend.upload_contracts import Finalize, validate_finalization
    from pydantic import ValidationError
    from test_activity import full_packet, A, U
    body=full_packet()
    middle=body.activity.events[-1].model_copy(deep=True)
    from uuid import uuid4
    middle.event_id=uuid4(); middle.event_type='resumed'
    body.activity.events.insert(1,middle)
    for i,event in enumerate(body.activity.events): event.event_sequence=i
    row={'id':A,'song_id':'song','song_source':'builtin'}
    with pytest.raises(HTTPException,match='Resume requires'): validate_finalization(row,body,U)
    value=body.model_dump(mode='json'); value['activity']['events'][0]['occurred_at']='2026-01-01T00:00:00'
    with pytest.raises(ValidationError): Finalize.model_validate(value)


def test_v1_upload_contract_is_explicitly_rejected():
    from pianokt_backend.upload_contracts import Finalize
    from pydantic import ValidationError
    with pytest.raises(ValidationError): Finalize.model_validate({'cycle':{'events':[]}})
