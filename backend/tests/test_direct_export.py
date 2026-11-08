import json
import pytest
from test_attempt_dataset import fixture
from pianokt_backend.contracts import digest
from pianokt_backend.pipeline.direct import CommittedResults
from pianokt_backend.pipeline.attempts import export_attempts
from pianokt_backend.pipeline.training import export_dataset
from pianokt_backend.publication import read_bundle

CUTOFF = '2026-02-01T00:00:00Z'


def setup(tmp_path):
    store, lake, context = fixture(tmp_path)
    rows = []
    # Add the worker envelope to immutable fixture objects using a fresh root.
    from pianokt_backend.storage import Objects
    direct_store = Objects(str(tmp_path / 'direct'))
    for key in store.keys('alignment'):
        r = json.loads(store.read(key))
        r.update(schema_version=3, performance_hash='performance',
                 matcher_version='test-v3', config={'version':'test-v3'},
                 summary={'alignment_quality_valid':r['alignment_quality_valid']})
        direct_store.json(key, r)
        rows.append(dict(source_environment='production',attempt_id=r['attempt_id'], learner_key='learner',
                         alignment_run_id=r['alignment_run_id'], result_key=key,
                         reference_hash='score', performance_hash='performance',
                         result_digest=digest(direct_store.read(key)),
                         result_committed_at='2026-01-02T02:01:00Z', summary=r['summary']))
    return direct_store, lake, context, rows


def test_direct_matches_lake_without_listing_and_retries(tmp_path):
    store, lake, context, rows = setup(tmp_path)
    export_attempts(store,lake,CUTOFF,'datasets/lake',context)
    expected = read_bundle(store,'datasets/lake')[1]['attempts.jsonl']
    store.keys = lambda _: pytest.fail('Direct export must not scan raw objects')
    source = CommittedResults(rows)
    result = export_attempts(store,source,CUTOFF,'datasets/direct',context)
    assert read_bundle(store,'datasets/direct')[1]['attempts.jsonl'] == expected
    assert result['source'] == 'postgres-ready-v1'
    assert export_attempts(store,source,CUTOFF,'datasets/direct',context) == result
    export_dataset(store,source,CUTOFF,'datasets/training.json',context)
    data = json.loads(store.read('datasets/training.json'))
    assert len(data['examples']) == 4
    assert [e['attempt_id'] for e in data['examples']] == ['a1','a1','a3','a3']


@pytest.mark.parametrize('failure', ['missing','digest','identity','schema','legacy','selection','cutoff','json'])
def test_direct_integrity_failures_never_publish(tmp_path,failure):
    store, _, context, rows = setup(tmp_path)
    selection = None
    if failure == 'missing':
        rows[0]['result_key'] = 'alignment/missing/result.json'
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
        export_attempts(store,CommittedResults(rows),CUTOFF,'datasets/fail',context,selection)
    assert store.keys('datasets') == []


def test_direct_rejects_wrong_catalog_content_before_publication(tmp_path):
    store, _, context, rows = setup(tmp_path)
    row=rows[0]
    row.update(song_id=context['attempts'][0]['song_id'],segment_id='different-segment',segment_index=1,midi_sha256='a'*64)
    with pytest.raises(ValueError,match='identity/schema mismatch'):
        export_attempts(store,CommittedResults(rows),CUTOFF,'datasets/wrong-content',context)
