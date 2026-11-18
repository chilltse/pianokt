import pytest
from pianokt_backend.alignment.additive_rescue import rescue_rows
from pianokt_backend.contracts import AlignmentConfig
from pianokt_backend.alignment.reference_grouping import load_reference_grouping_policy
from pianokt_backend.alignment.alignment_policies import load_alignment_policy
from pianokt_backend.alignment.assessment_policies import load_assessment_policy


def test_shared_policy_defaults():
    config = AlignmentConfig()
    policy = load_alignment_policy()
    assert config.version == policy['version'] == '2.0.0'
    assert config.assessment_version == '1.0.0'
    assert config.timing_threshold_ms == load_assessment_policy()['timing']['max_tolerance_ms'] == 200
    assert config.additive_rescue_window_sec == .3
    assert 'correct_threshold_ms' not in policy
    assert 'perfect_threshold_ms' not in policy
    with pytest.raises(ValueError):
        AlignmentConfig(version='1.0.0')
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



def test_rescue_adds_only_an_unmatched_same_pitch_at_inclusive_boundary():
    anchor = dict(alignment_type="match", reference_chord_index=0, ref_pitch=60,
                  performance_pitch=60, ref_onset_sec=1.0, performance_onset_sec=1.0)
    wrong = dict(anchor, ref_pitch=65, performance_pitch=67)
    missing = dict(alignment_type="deletion", reference_chord_index=0, ref_pitch=67,
                   ref_onset_sec=1.0, ref_onset_tick=1000, ref_id="r0_n1")
    extra = dict(alignment_type="insertion", performance_pitch=67,
                 performance_onset_sec=1.3, performance_id="p_n2",
                 performance_chord_index=1)
    rows = [anchor, wrong, missing, extra]
    result = rescue_rows(rows, .3)
    assert result == [anchor, wrong, dict(missing, **extra) | {"alignment_type": "match"}]
    assert rows[2]["alignment_type"] == "deletion"
    extra["performance_onset_sec"] = 1.300001
    assert rescue_rows(rows, .3) == rows
