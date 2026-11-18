"""The visual report scenarios are also executable regression tests."""
from pathlib import Path

from pianokt_backend.alignment.visual_cases import CASES, run_case


def test_visual_cases_preserve_transport_timing_and_window(tmp_path: Path):
    results = {case.id: run_case(case, tmp_path) for case in CASES}
    late = results['constant-late']
    assert late['diagnostics']['global_offset_sec'] == 0
    assert round(late['diagnostics']['raw_timing_median_sec'], 3) == 0.12
    assert min(note['raw_timing_deviation_sec'] for note in results['early']['notes'] if note['alignment_type'] == 'match') < 0
    assert results['missed-note']['summary']['missing_notes'] == 1
    assert results['extra-note']['summary']['extra_notes'] == 1
    chord_rows = results['extra-note-in-chord']['notes']
    assert {(row['ref_pitch'], row['performance_pitch']) for row in chord_rows if row['alignment_type'] == 'match'} == {(60, 60), (64, 64)}
    assert [(row['ref_pitch'], row['performance_pitch']) for row in chord_rows if row['alignment_type'] == 'insertion'] == [(None, 61)]
    assert results['repeated-late-phrase']['diagnostics']['reference_notes_observed'] == 3
    assert results['repeated-late-phrase']['diagnostics']['reference_notes_excluded'] == 3
    assert results['seeked-window']['notes'][0]['performance_player_song_time_sec'] >= 10
    assert results['incomplete-transport']['summary']['alignment_quality_valid'] is False
    assert 'invalid_transport_segment_audit' in results['incomplete-transport']['summary']['alignment_quality_reason']
    assert results['rewound-transport']['summary']['alignment_quality_valid'] is False
    assert 'non_monotonic_transport' in results['rewound-transport']['summary']['alignment_quality_reason']
