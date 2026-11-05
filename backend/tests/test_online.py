from pianokt_backend import online
from contextlib import nullcontext


def test_connect_requests_lossless_float_output(monkeypatch):
    captured = {}
    sentinel = object()
    def connect(dsn, **kwargs):
        captured.update(kwargs)
        return nullcontext(sentinel)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    monkeypatch.setattr(online.psycopg, 'connect', connect)
    with online.connect() as connection:
        assert connection is sentinel
    assert captured['options'] == '-c extra_float_digits=1'
