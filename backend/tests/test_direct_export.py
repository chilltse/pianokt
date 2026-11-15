import json
import pytest
from test_attempt_dataset import fixture
from pianokt_backend.contracts import digest
from pianokt_backend.pipeline.direct import CommittedResults
from pianokt_backend.pipeline.attempts import export_attempts
from pianokt_backend.pipeline.training import export_dataset
from pianokt_backend.publication import read_bundle
from v3_test_helpers import bundle_file, bundle, training_file

CUTOFF = '2026-02-01T00:00:00Z'


def setup(tmp_path):
    store, lake, context = fixture(tmp_path)
    rows = []
    # Add the worker envelope to immutable fixture objects using a fresh root.
    from pianokt_backend.storage import Objects
    direct_store = Objects(str(tmp_path / 'direct'))
    for key in store.keys('silver/alignment'):
        r = json.loads(store.read(key))
        r.update(data_architecture_version='3',schema_version=3, performance_hash='performance',
                 matcher_version='test-v3', config={'version':'test-v3'},
                 summary={'alignment_quality_valid':r['alignment_quality_valid']})
        direct_store.json(key, r)
        rows.append(dict(data_architecture_version='3',source_environment='production',attempt_id=r['attempt_id'], learner_key='learner',
                         alignment_run_id=r['alignment_run_id'], result_key=key,
                         reference_hash='score', performance_hash='performance',
                         result_digest=digest(direct_store.read(key)),
                         result_committed_at='2026-01-02T02:01:00Z', summary=r['summary']))
    return direct_store, lake, context, rows


def test_direct_no_listing_and_idempotent_publication(tmp_path):
    store, _, context, rows = setup(tmp_path)
    source = CommittedResults(rows)
    original_keys = store.keys
    store.keys = lambda _: pytest.fail('Direct export must not scan objects')
    result = export_attempts(store,source,CUTOFF,None,context)
    assert result['source'] == 'postgres-registry-v3'
    assert export_attempts(store,source,CUTOFF,None,context) == result
    training = export_dataset(store,source,CUTOFF,None,context)
    store.keys = original_keys
    data = json.loads(training_file(store))
    assert len(data['examples']) == 4
    assert [e['attempt_id'] for e in data['examples']] == ['a1','a1','a3','a3']


@pytest.mark.parametrize('failure', ['missing','digest','identity','schema','legacy','selection','cutoff','json'])
def test_direct_integrity_failures_never_publish(tmp_path,failure):
    store, _, context, rows = setup(tmp_path)
    selection = None
    if failure == 'missing':
        rows[0]['result_key'] = 'silver/alignment/missing/result.json'
        rows[0]['alignment_run_id'] = 'missing'
    elif failure == 'digest': rows[0]['result_digest'] = 'bad'
    elif failure == 'identity': rows[0]['learner_key'] = 'other'
    elif failure == 'schema': rows[0]['reference_hash'] = 'other'
    elif failure == 'legacy': rows[0]['result_digest'] = None
    elif failure == 'selection': selection = {'a1':'historical'}
    elif failure == 'cutoff':
        rows[0]['result_committed_at'] = CUTOFF
        selection = {'a1':'a1'}
    elif failure == 'json':
        rows[0]['result_digest'] = digest(b'broken')
        read = store.read
        store.read = lambda k: b'broken' if k == rows[0]['result_key'] else read(k)
    with pytest.raises((ValueError,FileNotFoundError)):
        export_attempts(store,CommittedResults(rows),CUTOFF,None,context,selection)
    assert store.keys('gold') == []


def test_direct_rejects_wrong_catalog_content_before_publication(tmp_path):
    store, _, context, rows = setup(tmp_path)
    row=rows[0]
    row.update(song_id=context['attempts'][0]['song_id'],segment_id='different-segment',segment_index=1,midi_sha256='a'*64)
    with pytest.raises(ValueError,match='identity/schema mismatch'):
        export_attempts(store,CommittedResults(rows),CUTOFF,None,context)


def test_training_retains_attempt_context_for_older_results(tmp_path):
    store, _, context, rows = setup(tmp_path)
    for row in rows:
        row['metadata'] = {'settings': {'midi_keyboard_used': False}}
    export_dataset(store,CommittedResults(rows),CUTOFF,None,context)
    data = json.loads(training_file(store))
    for run in data['runs']:
        expected = next(a for a in context['attempts'] if a['attempt_id'] == run['attempt_id'])
        assert run['midi_keyboard_used'] is False
        assert run['visit_id'] == expected['visit_id']
        assert run['reference_events_count'] == 2
    # The grouping tool preserves the context at the attempt level.
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'scripts/tse/group_pianokt_by_student.py'
    spec = importlib.util.spec_from_file_location('group_students', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    grouped = module.group_export(data)
    assert grouped['students'][0]['visits'][0]['visit_id'] == 'v1'
    assert grouped['students'][0]['visits'][1]['visit_id'] == 'v2'
    assert all(v['ordering_source'] == 'visit_started_at' for v in grouped['students'][0]['visits'])
    shuffled = {**data, 'examples': list(reversed(data['examples'])), 'runs': list(reversed(data['runs']))}
    assert module.group_export(shuffled) == grouped
    import copy
    duplicates = copy.deepcopy(data)
    duplicates['examples'].append(duplicates['examples'][0])
    with pytest.raises(ValueError, match='duplicate event_index'):
        module.group_export(duplicates)
    missing_visit = copy.deepcopy(data)
    missing_visit['runs'][0]['visit_id'] = None
    with pytest.raises(ValueError, match='Missing visit_id'):
        module.group_export(missing_visit)
    for student in grouped['students']:
        for attempt in [a for v in student['visits'] for a in v['attempts']]:
            assert attempt['midi_keyboard_used'] is False
            assert attempt['reference_events_count'] == 2
            assert attempt['visit_id'] is not None


def test_training_context_does_not_invent_missing_keyboard():
    from pianokt_backend.pipeline.training import training_attempt_context
    result = {'events_data': {'onset_seconds': [0, 1]}}
    assert training_attempt_context(result) == dict(
        midi_keyboard_used=None, visit_id=None, reference_events_count=2)
    with pytest.raises(ValueError, match='Visit context'):
        training_attempt_context({**result, 'visit_id':'wrong'}, {'visit_id':'original'})
    with pytest.raises(ValueError, match='Reference event count'):
        training_attempt_context({**result, 'reference_events_count':3})
    with pytest.raises(ValueError, match='keyboard context'):
        training_attempt_context({**result, 'midi_keyboard_used':True,
                                  '_attempt_settings':{'midi_keyboard_used':False}})


def test_training_and_grouping_preserve_detailed_event_responses(tmp_path):
    from pathlib import Path
    import importlib.util
    from pianokt_backend.storage import Objects
    store, _, context, rows = setup(tmp_path)
    detailed = json.loads((Path(__file__).resolve().parents[2] / 'pianokt_attempt_example_v1_1.json').read_text())['events_data']
    # Publish a new fixture root so the committed artifact remains immutable.
    source = Objects(str(tmp_path / 'detailed'))
    for row in rows:
        result = json.loads(store.read(row['result_key']))
        result['events_data'] = detailed
        source.json(row['result_key'], result)
        row['result_digest'] = digest(source.read(row['result_key']))
    export_dataset(source,CommittedResults(rows),CUTOFF,None,context)
    payload = json.loads(training_file(source))
    spec = importlib.util.spec_from_file_location('group_students',
        Path(__file__).resolve().parents[2] / 'scripts/tse/group_pianokt_by_student.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    grouped = module.group_export(payload)
    for student in grouped['students']:
        for attempt in [a for v in student['visits'] for a in v['attempts']]:
            assert attempt['reference_events_count'] == len(detailed['onset_seconds'])
            for key in ('pitches','onset_seconds','intervals_seconds',
                        'timing_offset_ms','pitch_offset_semitones','extra_notes','reject_reason'):
                assert attempt['events_data'][key] == detailed[key]
            assert attempt['events_data']['correct'] == [
                1 - reason for reason in detailed['reject_reason']['event']]


def test_training_bad_response_shape_aborts_publication(tmp_path):
    store, lake, context, _ = setup(tmp_path)
    original = store.read
    def malformed(key):
        raw = original(key)
        if key == 'silver/alignment/a1/result.json':
            result = json.loads(raw)
            result['events_data']['timing_offset_ms'][0] = [[], []]
            return json.dumps(result).encode()
        return raw
    store.read = malformed
    with pytest.raises(ValueError, match='hand shape: timing'):
        export_dataset(store,lake,CUTOFF,None,context)
    assert store.keys('gold') == []
