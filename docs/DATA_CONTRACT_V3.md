# V3 数据契约

架构版本与算法版本独立。DATA_ARCHITECTURE_VERSION 为字符串 `3`；Result 的既有 schema_version 为整数 3。Activity、上传与 Product Results 契约不因本次架构改变。

## Raw

reference_hash / performance_hash / performance_events_hash 使用原始上传字节 SHA-256。GCS generation 在 Worker 校验时固定；create-only PUT 不覆盖已有不同内容。新 Raw 路径包含 learner_key / attempt_id，Worker 拒绝跨身份路径。Reference 是实际演奏快照，不随曲库更新改变。

## Silver

Result 保留 attempt_id、learner_key、song_id、segment_id、segment_index、midi_sha256（曲目版本）、score_version（Reference 快照 hash）、performance_hash、performance_events_hash、alignment_run_id、alignment_version、matcher_version、reference_grouping_version、kc_labeler_version、config、alignment_context、diagnostics、summary、notes、events_data、source_environment。

`events_data` 的 onset_seconds、pitches、intervals_seconds、kc_vectors、reject_reason、timing_offset_ms、pitch_offset_semitones、extra_notes 及 KC feature names 原样保留。正确性为 `1 - reject_reason.event`，不重新解释标签。

frontend_note_accuracy 和 backend_event_accuracy 均为 [0,1]；前者来自前端，后者为质量合格时 event label 为 0 的比例。无有效事件或质量失败为 null。challenge_recordings.accuracy_pct 保持百分制；旧成绩不重标为 Event Accuracy。

Registry 字段详见 migration 024。result_digest 是完整 canonical JSON 字节 SHA-256。config_digest 是 stable_id(config)。song_version 不新增重复字段，使用已有 midi_sha256。数据库同时校验 Registry 与 Attempt 的 owner、environment、Song/Segment 和输入 hashes。

`processing_purpose` 为 online / research，首次提交后不可变。线上 pointer 仅能指向同 attempt 的 online Run。已提交不代表质量合格，质量合格不代表满足某个训练集政策。

## Gold

Attempt bundle 文件：attempts.jsonl、visits.json、attempt_events.json、attempt_metadata.json、index_map.json、intent.json、manifest.json。

KT bundle 文件：training.json、intent.json、manifest.json。training.json 内部 examples / runs 消费格式保留；每个 onset 一条 example，不改变词表、PID 或模型定义。

Manifest 包含 dataset_version、dataset_type、schema_version、data_architecture_version、created_at、cutoff、source_environment、source_selection、各算法版本集合、quality_policy_version、ordering_policy、source_runs、source_snapshot_digest、student/visit/attempt/event_count、excluded_count、exclusion_reasons、files、publication_protocol。files 是文件名到 SHA-256 的映射。

created_at 记录首次发布 intent 的时间，不参与 dataset_version 计算。重试校验所有内容与规则字段并复用原始 created_at；同一来源、规则、输出具有同一 dataset_version。部分文件写入不构成数据集。消费者必须校验 intent 与 manifest 一致及所有文件 hash。

选择的 cutoff 同时限制提交时间与活动 accepted/occurred 时间。默认每个 Attempt 当前正式 online Run；显式版本选择从 Registry 查询，多个匹配 Run 必须指定 run-selection，不能任意选最新。旧 legacy 与未提交对象永不成为样本。
