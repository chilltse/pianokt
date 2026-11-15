import pytest
from pianokt_backend.contracts import AlignmentConfig
from pianokt_backend.alignment.reference_grouping import load_reference_grouping_policy
from pianokt_backend.alignment.alignment_policies import load_alignment_policy


def test_shared_policy_defaults():
    config = AlignmentConfig()
    policy = load_alignment_policy()
    assert config.timing_threshold_ms == policy['correct_threshold_ms'] == 200
    assert config.performance_chord_grouping_sec == .05
    assert config.chord_onset_tolerance_sec == .03
    assert config.match_window_sec == 1
    assert config.wrong_pitch_window_sec == .3
    assert config.insertion_attach_window_sec == .3
    assert 'onset_grouping_ms' not in policy


@pytest.mark.parametrize('version', ['unknown', 'time-guided-chord-monotonic-v3', ''])
def test_unknown_alignment_rejected(version):
    with pytest.raises(ValueError):
        AlignmentConfig(version=version)


def test_policies_are_independent_copies():
    policy = load_reference_grouping_policy()
    policy['onset_grouping_ms'] = 999
    assert load_reference_grouping_policy()['onset_grouping_ms'] == 30
    with pytest.raises(ValueError):
        load_reference_grouping_policy('unknown')
