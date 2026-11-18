from pianokt_backend.accuracy import backend_event_accuracy


def test_event_accuracy():
    data = {'reject_reason': {'event': [0, 1, 0], 'note': []}}
    assert backend_event_accuracy(data, True) == 2 / 3
    assert backend_event_accuracy(data, False) is None
    assert backend_event_accuracy({'reject_reason': {'event': []}}, True) is None
    assert backend_event_accuracy({'reject_reason': {'event': [1]}}, True) == 0
    assert backend_event_accuracy({'accuracy': .9}, True) is None


def test_projection_preserves_frontend_and_event_units():
    from types import SimpleNamespace
    from pianokt_backend.projections import project_challenge
    from test_activity import full_packet, A, U
    calls = []
    def execute(query, params):
        assert query.count('%s') == len(params)
        calls.append((query, params))
        return SimpleNamespace(fetchone=lambda: None)
    row = dict(id=A, user_id=U, song_source='local', song_id='song', status='READY',
               validated_at=False, source_environment='test',
               summary=dict(alignment_quality_valid=True, backend_event_accuracy=.5),
               metadata=dict(duration_sec=1, settings=dict(frontend_note_accuracy=.8)))
    project_challenge(SimpleNamespace(execute=execute), row, full_packet())
    params = next(p for q, p in calls if 'insert into public.challenge_recordings' in q)
    assert params[8] == '50.0'
    assert params[-2:] == (.8, 'backend_event_accuracy')
