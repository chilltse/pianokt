import pytest
from pianokt_backend.provenance import recording_environment,export_environment


def test_provenance_is_fail_closed(monkeypatch):
    monkeypatch.delenv('PIANOKT_ENV',raising=False)
    assert recording_environment()=='development'
    monkeypatch.setenv('PIANOKT_ENV','prod')
    with pytest.raises(ValueError): recording_environment()
    with pytest.raises(ValueError): export_environment('unknown')
    monkeypatch.setenv('PIANOKT_ENV','production')
    assert recording_environment()=='production'
