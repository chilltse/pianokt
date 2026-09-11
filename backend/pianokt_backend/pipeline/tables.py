import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError

ENVELOPE_SCHEMA=pa.schema([(k,pa.string()) for k in ['event_id','event_type','source_key','payload_json']])
NOTE_SCHEMA=pa.schema([(k,pa.string()) for k in ['alignment_event_id','alignment_run_id','attempt_id','learner_key','song_id','score_version','performance_hash','alignment_version','alignment_type']]+[(k,pa.float64()) for k in ['ref_pitch','performance_pitch','ref_onset_sec','performance_onset_sec','performance_onset_sec_global_aligned','timing_deviation_global_aligned']]+[('reject_reason',pa.int32()),('is_correct',pa.bool_()),('within_observed_range',pa.bool_()),('timing_labels_valid',pa.bool_()),('payload_json',pa.string())])
HAND_SCHEMA=pa.schema([(k,pa.string()) for k in ['event_id','alignment_run_id','attempt_id','learner_key','song_id','hand']]+[('reject_reason',pa.int32()),('timing_offset',pa.float64()),('pitch_offset',pa.float64()),('duration',pa.float64()),('onset_time',pa.float64()),('pitches',pa.list_(pa.int32()))])
SUMMARY_SCHEMA=pa.schema([(k,pa.string()) for k in ['alignment_run_id','attempt_id','learner_key','song_id','score_version','alignment_version']]+[(k,pa.int64()) for k in ['expected_notes','correct_notes','extra_notes','missing_notes']]+[('accuracy',pa.float64()),('timing_labels_valid',pa.bool_())])

class Tables:
    def __init__(self,root): self.root=root.rstrip('/')
    def merge(self,name,rows,schema,key):
        if not rows: return
        source=pa.Table.from_pylist(rows,schema=schema)
        if len(set(source[key].to_pylist()))!=len(rows): raise ValueError('Duplicate source keys')
        path=self.root+'/'+name
        try: table=DeltaTable(path)
        except TableNotFoundError:
            write_deltalake(path,source,mode='error'); return
        table.merge(source, predicate=f't.{key}=s.{key}',source_alias='s',target_alias='t').when_matched_update_all().when_not_matched_insert_all().execute()
    def rows(self,name):
        try: return DeltaTable(self.root+'/'+name).to_pyarrow_table().to_pylist()
        except TableNotFoundError: return []
