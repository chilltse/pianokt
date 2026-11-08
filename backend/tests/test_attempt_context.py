from pianokt_backend.attempt_context import attempt_context, attach_attempt_context


def test_context_uses_stored_settings_and_finalized_visit():
    context = attempt_context(dict(id='attempt', song_id='song', metadata={
        'settings': {'midi_keyboard_used': False}}, finalization={
        'activity': {'visit': {'id': 'visit'}}}))
    assert context == dict(attempt_id='attempt', song_id='song', midi_keyboard_used=False, visit_id='visit',segment_id=None,segment_index=None,midi_sha256=None)
    result = attach_attempt_context({'events_data': {'onset_seconds': [0, .5]}}, context)
    assert result['reference_events_count'] == 2
    assert result['visit_id'] == 'visit'


def test_missing_context_is_unknown_not_false_or_fabricated():
    context = attempt_context({'id': 'attempt'})
    assert context['midi_keyboard_used'] is None
    assert context['visit_id'] is None
    assert attach_attempt_context({'events_data': {'onset_seconds': []}}, context)['reference_events_count'] == 0


def test_local_debug_auto_loads_context(tmp_path, monkeypatch):
    import json
    import runpy
    from pathlib import Path
    from test_time_guided_alignment import write_midi, sidecar
    reference, performance = tmp_path/'reference.mid', tmp_path/'performance.mid'
    notes = [(60, 0, .1)]
    write_midi(reference, notes)
    write_midi(performance, notes)
    (tmp_path/'events.json').write_text(json.dumps(sidecar(notes)))
    (tmp_path/'attempt-context.json').write_text(json.dumps(dict(attempt_id='real-attempt',
        song_id='song', visit_id='visit', midi_keyboard_used=True,
        observed_end_sec=.5, reference_origin_sec=0)))
    output = tmp_path/'result'
    monkeypatch.setattr('sys.argv', ['alignment_debug.py', '--reference', str(reference),
        '--performance', str(performance), '--events', str(tmp_path/'events.json'), '--output', str(output)])
    script = Path(__file__).resolve().parents[2]/'tests/local_debug/alignment_debug.py'
    runpy.run_path(str(script), run_name='__main__')
    result = json.loads(output.with_suffix('.json').read_text())
    assert result['attempt_id'] == 'real-attempt'
    assert result['alignment_context']['observed_end_sec'] == .5
    assert result['song_id'] == 'song' and result['visit_id'] == 'visit'
    assert result['midi_keyboard_used'] is True
    assert result['reference_events_count'] == 1
