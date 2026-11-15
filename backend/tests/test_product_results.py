import pytest
from pydantic import ValidationError
from pianokt_backend.product_results import ProductResult


def payload(**changes):
    return dict(attempt_id='20000000-0000-0000-0000-000000000001', song_source='local', song_id='test',
                frontend_note_accuracy=.9, completion=1, is_completed=True, completion_reason='finished',
                started_at='2026-11-15T00:00:00Z', ended_at='2026-11-15T00:01:00Z', duration=60, **changes)


def test_product_is_independent_of_recording():
    assert ProductResult(**payload()).frontend_note_accuracy == .9


@pytest.mark.parametrize('changes', [dict(frontend_note_accuracy=1.1), dict(completion=-1), dict(is_completed=False), dict(completion=.99), dict(started_at='2026-11-16T00:00:00Z')])
def test_invalid_product_contract(changes):
    data=payload(); data.update(changes)
    with pytest.raises(ValidationError): ProductResult(**data)
