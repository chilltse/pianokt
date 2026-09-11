from fastapi.testclient import TestClient
from pianokt_backend.api import app,user

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
    uid='00000000-0000-0000-0000-000000000001'
    monkeypatch.setattr(httpx,'get',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:{'id':uid}))
    monkeypatch.setattr(httpx,'post',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:False))
    with pytest.raises(HTTPException) as exc: user('Bearer fixture')
    assert exc.value.status_code==403
    monkeypatch.setattr(httpx,'post',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:True))
    assert user('Bearer fixture')==uid
