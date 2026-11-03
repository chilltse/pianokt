import os
import json
import pytest

@pytest.mark.skipif(os.getenv('PIANOKT_TEST_SPARK')!='1',reason='Explicit Spark integration gate')
def test_checkpoint_and_quarantine(tmp_path):
    from pianokt_backend.pipeline.spark_events import run
    from deltalake import DeltaTable
    raw=tmp_path/'raw'; events=raw/'events'; events.mkdir(parents=True)
    (events/'001.jsonl').write_text(json.dumps(dict(event_id='e1',event_type='practice.event'))+'\ninvalid-json\n')
    lake=str(tmp_path/'lake'); checkpoint=str(tmp_path/'checkpoints')
    run(str(raw),lake,checkpoint)
    assert DeltaTable(lake+'/bronze/practice_events').to_pyarrow_table().num_rows==2
    assert DeltaTable(lake+'/quality/practice_events').to_pyarrow_table().num_rows==1
    assert DeltaTable(lake+'/silver/practice_events').to_pyarrow_table().num_rows==1
    (events/'002.jsonl').write_text(json.dumps(dict(event_id='e2',event_type='practice.event'))+'\n')
    run(str(raw),lake,checkpoint)
    assert DeltaTable(lake+'/silver/practice_events').to_pyarrow_table().num_rows==2
    from pianokt_backend.pipeline.tables import Tables
    assert len(Tables(lake).rows('silver/practice_events'))==2
    assert DeltaTable(lake+'/ops/practice_event_commits').to_pyarrow_table().num_rows==2
    (events/'003.jsonl').write_text(json.dumps(dict(event_id='e1',event_type='changed'))+'\n'+json.dumps(dict(event_id='e3',event_type='practice.event'))+'\n')
    with pytest.raises(Exception,match='Immutable practice event conflict'):
        run(str(raw),lake,checkpoint)
    assert len(Tables(lake).rows('silver/practice_events'))==2
    assert DeltaTable(lake+'/ops/practice_event_commits').to_pyarrow_table().num_rows==2
