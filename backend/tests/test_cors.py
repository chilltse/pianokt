"""CORS must apply to preflight, auth errors and unhandled route failures."""
import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pianokt_backend.api import frontend_origins, request_timing


@pytest.fixture
def client():
    app=FastAPI()
    app.middleware('http')(request_timing)
    app.add_middleware(CORSMiddleware,allow_origins=frontend_origins('http://localhost:5173, http://127.0.0.1:5173/'),
        allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'],expose_headers=['X-Request-ID'])
    @app.post('/activity')
    def activity(): raise RuntimeError('database diagnostic fixture')
    @app.get('/auth-error')
    def auth_error(): raise HTTPException(401,'Invalid session')
    return TestClient(app)


@pytest.mark.parametrize('origin',['http://localhost:5173','http://127.0.0.1:5173'])
def test_preflight_allows_local_frontend_and_authorization_header(client,origin):
    response=client.options('/activity',headers={'Origin':origin,'Access-Control-Request-Method':'POST',
        'Access-Control-Request-Headers':'authorization,content-type'})
    assert response.status_code==200
    assert response.headers['access-control-allow-origin']==origin


def test_unhandled_failure_keeps_cors_and_correlated_logs(client,caplog):
    with caplog.at_level(logging.ERROR):
        response=client.post('/activity',headers={'Origin':'http://localhost:5173'})
    assert response.status_code==500
    assert response.headers['access-control-allow-origin']=='http://localhost:5173'
    request_id=response.json()['request_id']
    assert response.headers['x-request-id']==request_id
    assert request_id in caplog.text and 'database diagnostic fixture' in caplog.text
    assert 'database diagnostic fixture' not in response.text


def test_auth_error_is_readable_from_frontend(client):
    response=client.get('/auth-error',headers={'Origin':'http://localhost:5173'})
    assert response.status_code==401
    assert response.headers['access-control-allow-origin']=='http://localhost:5173'
    assert 'X-Request-ID' in response.headers['access-control-expose-headers']


def test_unlisted_origin_is_not_allowed(client):
    response=client.options('/activity',headers={'Origin':'https://unlisted.invalid','Access-Control-Request-Method':'POST'})
    assert response.status_code==400
    assert 'access-control-allow-origin' not in response.headers


def test_origin_config_trims_spaces_trailing_slashes_and_duplicates():
    assert frontend_origins(' http://localhost:5173/ ,http://localhost:5173, http://127.0.0.1:5173/ ')==[
        'http://localhost:5173','http://127.0.0.1:5173']
