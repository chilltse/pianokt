from types import SimpleNamespace
import pytest
from pianokt_backend.segment_contract import validate_segment_bounds


def validate(notes, bounds):
    validate_segment_bounds([SimpleNamespace(start=a, end=b) for a,b in notes], bounds)


@pytest.mark.parametrize('bounds', [[(1,2)],[(0,3)],[(1,1.5),(1.5,2)],[(1,1.2),(1.8,2)]])
def test_only_silence_can_be_omitted(bounds):
    validate([(1,1.2),(1.8,2)], bounds)


@pytest.mark.parametrize('bounds', [[(1.01,2)],[(1,1.99)],[(1,1.1),(1.1,2)],[(1,1.1),(1.3,2)],[(1,2),(1.9,3)],[(float('nan'),2)]])
def test_omitted_and_cross_boundary_notes_rejected(bounds):
    with pytest.raises(ValueError): validate([(1,1.2),(1.8,2)], bounds)


def test_even_short_notes_cannot_be_omitted():
    with pytest.raises(ValueError): validate([(1,1.001)], [(1.0011,2)])
