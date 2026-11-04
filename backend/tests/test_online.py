from pianokt_backend import online


def test_connect_requests_lossless_float_output(monkeypatch):
    captured = {}
    sentinel = object()
    def connect(dsn, **kwargs):
        captured.update(kwargs)
        return sentinel
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    monkeypatch.setattr(online.psycopg, 'connect', connect)
    assert online.connect() is sentinel
    assert captured['options'] == '-c extra_float_digits=1'
