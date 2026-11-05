"""Resource reuse must preserve refresh, rollback and shutdown behavior."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from google.auth.credentials import TokenState
from pianokt_backend import api, online


def test_concurrent_signers_refresh_once_and_refresh_expired_token(monkeypatch):
    class Credentials:
        token_state = TokenState.INVALID
        token = None
        refreshes = 0
        def refresh(self, request):
            self.refreshes += 1
            self.token = f'token-{self.refreshes}'
            self.token_state = TokenState.FRESH
    credentials = Credentials()
    monkeypatch.setattr(api, 'signing_credentials', lambda: (credentials, object()))
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(lambda _: api.signing_token(), range(16))) == ['token-1'] * 16
    assert credentials.refreshes == 1
    credentials.token_state = TokenState.STALE
    assert api.signing_token() == 'token-2'


def test_pool_returns_connection_after_transaction_failure(monkeypatch):
    outcomes = []
    @contextmanager
    def transaction():
        try:
            yield
        except ValueError:
            outcomes.append('rollback')
            raise
        else:
            outcomes.append('commit')
    connection = SimpleNamespace(transaction=transaction)
    pool = SimpleNamespace(getconn=lambda: connection, putconn=lambda c: outcomes.append('returned'))
    monkeypatch.setattr(online, '_pool_enabled', True)
    monkeypatch.setattr(online, 'pool', lambda: pool)
    with pytest.raises(ValueError):
        with online.connect():
            raise ValueError('failed write')
    with online.connect():
        pass
    assert outcomes == ['rollback', 'returned', 'commit', 'returned']


def test_api_lifespan_health_without_database_configuration(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with TestClient(api.app) as client:
        assert client.get('/health').status_code == 200
        assert online._pool_enabled
    assert not online._pool_enabled


def test_pool_preserves_float_settings_and_disables_prepared_statements(monkeypatch):
    captured = {}
    def create(dsn, **kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setattr(online, '_pool', None)
    monkeypatch.setattr(online, 'ConnectionPool', create)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    monkeypatch.setenv('DB_POOL_MAX_SIZE', '3')
    assert online.pool() is online.pool()
    assert captured['max_size'] == 3
    assert captured['kwargs']['options'] == '-c extra_float_digits=1'
    assert captured['kwargs']['prepare_threshold'] is None
