from pianokt_backend.publication import read_bundle

def bundle(store, kind='attempts'):
    keys = [k for k in store.keys('gold/'+kind) if k.endswith('/manifest.json')]
    assert len(keys) == 1
    return read_bundle(store, keys[0].rsplit('/',1)[0])

def bundle_file(store, name):
    return bundle(store)[1][name]

def training_file(store):
    return bundle(store, 'knowledge_tracing')[1]['training.json']


def finalize_wire(body):
    value=body.model_dump(mode='json')
    value['activity']['attempt']={k:v for k,v in value['activity']['attempt'].items() if k in ('attempt_id','visit_attempt_sequence','started_at')}
    return value


def ready_fixture(c, aid):
    from pianokt_backend.run_registry import register_run
    row=c.execute('select * from piano_attempts where id=%s',(aid,)).fetchone()
    result=dict(data_architecture_version='3',schema_version=3,attempt_id=str(row['id']),learner_key=str(row['learner_key']),
        song_id=row['song_id'],segment_id=row.get('segment_id'),segment_index=row.get('segment_index'),midi_sha256=row.get('midi_sha256'),
        score_version=row['reference_hash'],performance_hash=row['performance_hash'],performance_events_hash=row.get('performance_events_hash'),source_environment=row['source_environment'],
        alignment_version='1.0.0',reference_grouping_version='1.0.0',matcher_version='test',kc_labeler_version='1.2.0',config={},
        alignment_run_id='receipt-fixture',alignment_quality_valid=False,alignment_quality_reason='test',summary={})
    key='silver/alignment/receipt-fixture/result.json'
    saved=register_run(c,row,key,result,'online')
    c.execute("update piano_attempts set status='READY',validated_at=now(),alignment_run_id=%s,result_key=%s,result_digest=%s,result_committed_at=%s where id=%s",(result['alignment_run_id'],key,saved['result_digest'],saved['committed_at'],aid))
