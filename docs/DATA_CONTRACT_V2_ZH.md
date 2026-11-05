# PianoKT v2 数据字典与流向

2026-11-05 最新架构：异步 finalize v3 + migration 017，详见 [异步接收与部署](ASYNC_FINALIZATION_ZH.md)。此前性能优化见 [计时说明](UPLOAD_PERFORMANCE_ZH.md)。

更新：2026-11-04。描述**代码 + migration 017 的目标结构**；本次没有在远端执行 migration 或删除对象。字段依据 migrations、API 模型和 Worker/Export 写入器。`?` 表示可空；未带 `?` 的 JSON 属性仍需以对应版本契约为准。

## 1. 版本和身份

- 创建上传：`schema_version=2`；异步 finalize：`schema_version=3`。前端本地 Bundle：`schemaVersion=2`。
- 按键侧车：`schemaVersion=1`；Result：`schema_version=3`；attempt 数据集 Manifest：`schema_version=1.3`；教育标签：v1.1。
- `attempt_id = piano_attempts.id = student_attempts.attempt_id = challenge_recordings.id = user_play_logs.session_id`，适用于 v2。不再要求客户端另传 session_id。
- 研究用户是 `learner_key`，不是 Auth UUID；这是可受控回连的假名化。
- `source_environment`：production/development/test/staging；历史数据库行可为 unknown。由 API 设置，客户端不能提供或覆盖它。未知来源默认不进入研究导出。
- 绝对时间使用带时区 timestamp，数据库存 timestamptz；歌曲和累计时长单位秒，浏览器/MIDI 原始时间戳单位毫秒。

## 2. Supabase：8 张活跃核心表 + 1 张历史表

### student_visits — 网站访问

| 字段 | 类型 / 含义 |
|---|---|
| id | uuid PK，Visit 身份 |
| user_id | uuid，FK auth.users |
| started_at / last_seen_at | timestamptz，开始与最近心跳 |
| ended_at | timestamptz?，观察到的离开时刻 |
| end_reason | text?，pagehide / signed_out 等 |
| end_is_inferred | boolean，是否推断结束 |
| last_sequence | bigint，防止迟到心跳覆盖新状态 |
| received_at | timestamptz，首次接收时刻 |
| source_environment | text，服务端来源 |

`unique(id,user_id)`；按用户/开始时间排序。`student_visit_status` 是视图，额外提供 `effective_ended_at`、`effective_end_is_inferred`；没有关闭信号时以 5 分钟阈值估计，不篡改源表。

### student_attempts — 开始过的演奏

| 字段 | 类型 / 含义 |
|---|---|
| attempt_id | uuid PK |
| user_id / visit_id | uuid，复合 FK 到 student_visits(id,user_id) |
| visit_attempt_sequence | bigint > 0，该 Visit 内演奏顺序 |
| song_id / song_source | text，曲目身份 |
| started_at | timestamptz，实际播放开始 |
| ended_at / end_reason | timestamptz? / text?，由最新事件派生 |
| received_at | timestamptz，首次接收 |
| source_environment | text，须与 Visit 来源相同 |
| sealed_at | timestamptz?，finalize 原子封存时间 |
| sealed_event_count | integer?，封存事件数量，至少 2，与 sealed_at 同时有值 |

`unique(visit_id,visit_attempt_sequence)`、`unique(attempt_id,user_id)`。没有 MIDI 上传也会存在；interrupted 不强制创建上传任务。

### student_attempt_events — 唯一生命周期事件权威

| 字段 | 类型 / 含义 |
|---|---|
| event_id | uuid PK，幂等身份 |
| attempt_id / user_id | uuid，复合 FK student_attempts |
| event_sequence | integer ≥ 0，`unique(attempt_id,event_sequence)` |
| event_type | text：play_started / paused / resumed / finished / exited / interrupted |
| occurred_at | timestamptz，客户端发生时间 |
| song_time_sec | double precision ≥ 0，歌曲位置 |
| elapsed_playing_sec | double precision ≥ 0，累计实际播放时长，暂停不累计 |
| metadata | jsonb，reason、客户端 success、来源曲目等补充证据 |
| received_at | timestamptz，首次入库时刻 |

环境通过 Attempt/Visit 继承。相同 ID 内容必须完全一致，首次 received_at 不因重试改变。最终有效 Cycle 从序号 0 连续，首尾为 play_started 和 finished/exited；interrupted 仅用于未封存演奏。

### piano_learner_keys — 研究身份映射

`user_id uuid PK/FK auth.users`、`learner_key uuid UNIQUE`。默认后端读写；不向研究文件导出 user_id 映射。

### piano_attempts — 上传与处理状态权威

| 字段 | 类型 / 含义 |
|---|---|
| id / user_id / learner_key | uuid，PK / Auth FK / 研究身份 |
| song_id / song_source / song_title | text / text / text? |
| metadata | jsonb，完整 v2 Upload 请求身份，不包含 Activity |
| performance_key / reference_key | text，GCS 相对 Key |
| performance_hash / reference_hash | text，SHA-256 |
| performance_events_key / performance_events_hash | text? / text?，按键侧车身份 |
| performance_events_count | integer?，按键事件数量 0..50000，侧车三个字段同有/同无 |
| status | text：CREATED / ACCEPTED / VALIDATING / UPLOADED（历史）/ PROCESSING / RETRY / INVALID / READY / FAILED |
| accepted_at / validated_at | timestamptz? / timestamptz?，持久接收/文件验证通过 |
| file_generations | jsonb?，各原始文件固定的 GCS generation |
| processing_attempts | integer，领取处理次数，最多 8 次尝试 |
| next_retry_at | timestamptz?，最早重试时间 |
| validation_error | text?，永久文件校验错误 |
| finalization | jsonb?，完整 v2 Finalize 请求，首次成功后不可变 |
| result_key / alignment_run_id | text? / text?，当前已提交结果指针 |
| result_digest | text?，Result 原始规范 JSON 字节的 SHA-256 |
| result_committed_at | timestamptz?，READY 提交时间 |
| summary | jsonb?，与 Result.summary 相同 |
| error_code | text?，最近处理错误分类 |
| lease_token / lease_until | uuid? / timestamptz?，防止失效 Worker 提交 |
| created_at / updated_at | timestamptz |
| source_environment | text，服务端不可重绑定的来源 |

`metadata` 字段为：schema_version、attempt_id、song_id、song_source、song_title、performance_hash、reference_hash、duration_sec、settings、performance_events_hash、performance_events_count。

`settings` 当前前端提供：range、assigned_range、observed_range（均为 start/end）、reference_time_origin_sec、waiting、transpose、time_basis、played_until_sec、left、right、client_accuracy_pct、client_difficulty、midi_keyboard_used。它们是被冻结的观测/设置，不代表服务端签发的考试任务。

`finalization = {schema_version: 3, activity: {owner_id, visit, attempt, events}}`；Visit/Attempt/Event 的客户端字段见第 4 节。保留这份封存身份是为了幂等提交和投影重建，不是第二个可独立修改的事件写入接口。

### challenge_recordings — 列表与评分投影

| 字段 | 类型 / 含义 |
|---|---|
| id / user_id | uuid PK / Auth FK |
| song_source / song_id / song_title | text / text / text? |
| duration_sec | numeric，声明的曲目时长，区别于实际 time_playing |
| midi_storage_path | text，新录音为 `gcs:{attempt_id}`，历史可为 Supabase Storage 路径 |
| midi_keyboard_used | boolean |
| accuracy_pct | numeric，Worker 质量通过后 accuracy × 100，pending/rejected 为 0 |
| client_accuracy_pct | numeric?，客户端即时分 |
| score_source | text：legacy / pending / aligned / rejected |
| difficulty | numeric，当前仍来自客户端曲目 metadata，未声称服务端校准 |
| source_environment | text，派生自 piano_attempts |
| created_at | timestamptz |

公开榜单仅纳入 production；挑战次数只统计对应 Attempt 已有 validated_at 的录音，正确率只聚合 aligned。质量拒绝不等于失败事务。客户端不能独立写权威投影；旧 save_challenge_recording RPC 的所有重载已撤销客户端执行权限。

### user_play_logs — 最终周期汇总投影

| 字段 | 类型 / 含义 |
|---|---|
| session_id / user_id | uuid PK（v2=attempt ID） / Auth FK |
| song_id / exercise_id / play_mode | text / text? / text；新记录为 challenge |
| days_since_signup | numeric?，相对注册时间的天数 |
| time_playing | numeric，终止事件 elapsed_playing_sec |
| song_time_sec | numeric?，终止事件歌曲位置 |
| challenge_recording_id | uuid?，FK challenge_recordings |
| is_played_in_full | boolean，仅终止事件 finished 为 true |
| completion_reason | text?，finished / exited；历史未知为 NULL |
| client_success | boolean?，终止事件 metadata.success，缺失为 NULL |
| exit_status | text，exited→abandoned；finished + true→succeeded，false→failed，缺失→unknown |
| started_at / ended_at | timestamptz，首尾事件 occurred_at |
| events_count | integer，完整事件数量 |
| projection_version | integer，v2 为 2，旧记录默认 1 |
| created_at / updated_at | timestamptz |

新汇总仅在 Worker 文件验证通过后创建；ACCEPTED 阶段没有 user_play_logs。环境从相同 ID 的 Attempt 继承。`exit_status` 是客户端判定，不会被 Worker 评分覆盖。写 numeric 汇总时使用完整十进制字符串，避免 float8 隐式转换舍入。重建命令不改变源事件和处理状态。

### piano_outbox — 原子事务消息

`event_id uuid PK`、`event_type text`、`aggregate_id text`、`payload jsonb`、`published_at timestamptz?`、`created_at timestamptz`；`unique(event_type,aggregate_id)`。

新 Challenge 消息：

- performance.submitted（历史为 performance.uploaded）：attempt_id、learner_key、source_environment，事务提交后才能发布。
- alignment.completed：attempt_id、alignment_run_id、result_key、source_environment。
- recommendation.impression/click：既有推荐反馈。

Relay 增补 event_id/event_type，发布成功再记 published_at；允许重复派发，消费者须幂等。新周期不再产生 practice.event；历史 outbox 未删除。

### play_events_raw — 只读历史归档

字段原样保留：`event_id uuid PK`、`session_id uuid`、`user_id uuid`、`song_id text`、`exercise_id text?`、`play_mode text`、`event_type text`、`song_time_sec numeric?`、`client_ts timestamptz?`、`metadata jsonb`、`created_at timestamptz`。

016 移除 `piano_play_outbox` trigger，关闭旧 log/aggregate 客户端 RPC，并撤销 backend 对该表的写权限。没有新 Challenge writer。原 JST 视图只是读取历史表，保留可用；后续确认无用途后可另做删除迁移。

## 3. Supabase：辅助业务表与托管系统

| 表 | 完整业务字段 | 用途 |
|---|---|---|
| profiles | id uuid PK/FK auth.users；email/display_name/avatar_url text?；created_at/updated_at timestamptz? | 用户资料，Auth 同步 |
| allowed_emails | email text PK | 白名单，由管理员维护 |
| piano_song_catalog | song_id text PK；title text；difficulty double precision（0..1）；genres text[]；enabled boolean | 推荐曲库 |
| piano_preferences | user_id uuid PK/FK auth.users；genres text[] | 用户偏好 |
| piano_recommendations | id uuid PK；user_id uuid FK；attempt_id uuid? FK piano_attempts；model_version text；response jsonb；created_at timestamptz | 演示推荐结果，response含is_demo、input_alignment_run_ids、items |
| piano_recommendation_feedback | event_id uuid PK；recommendation_id uuid FK；user_id uuid FK；song_id text；event_type text（impression/click）；created_at timestamptz | 推荐反馈与幂等 outbox |

这合计 **15 张项目 public 表**：8 核心 + 1 归档 + 6 辅助。`auth.users`、`storage.objects` 等由 Supabase 管理，不是本次设计的新表。avatars 桶用于头像；旧 challenge-recordings Storage 桶保留下载兼容，新的演奏文件不写进去。

## 4. HTTP v2 契约

`POST /activity`：

```json
{
  "owner_id": "Auth UUID",
  "visit": {"id": "UUID", "started_at": "带时区时间", "occurred_at": "带时区时间", "sequence": 1, "state": "active"},
  "attempt": {"attempt_id": "UUID", "visit_attempt_sequence": 1, "song_id": "曲目ID", "song_source": "builtin", "started_at": "带时区时间"},
  "events": [{"event_id": "UUID", "event_sequence": 0, "event_type": "play_started", "occurred_at": "带时区时间", "song_time_sec": 0, "elapsed_playing_sec": 0, "metadata": {}}]
}
```

这是字段示意，UUID/时间占位符需替换。Visit-only 心跳可不带 attempt 且 events=[]。Visit state 可为 active/pagehide/signed_out。

`POST /attempts`：`schema_version=2` 加 piano_attempts.metadata 所列上传字段。后端返回 attempt_id、performance_url、reference_url，以及提供侧车时的 performance_events_url。未知字段（例如旧 session_id/activity）拒绝。

`POST /attempts/{id}/finalize`：`{schema_version:3, activity:完整封存Activity}`，返回 202 `{id,status,validated_at}`，不访问 GCS。activity 必须有 Attempt 和至少首尾两个事件；只出现一次 finished/exited 终止。字段来源和实时包完全相同。

## 5. GCS raw：对象，不是 SQL 表

| 对象 Key | 内容 / 字段 | 写入与读取 |
|---|---|---|
| midi/{learner_key}/{attempt_id}/performance.mid | 演奏 MIDI 二进制 | 浏览器 create-only PUT；Worker核验 SHA-256/大小/格式并固定 generation |
| midi/{learner_key}/{attempt_id}/reference.mid | 冻结设置和观察范围裁剪的参考 MIDI | 同上，本次不改参考证据策略 |
| midi/{learner_key}/{attempt_id}/performance-events.json | schemaVersion、events、transportSegments | 浏览器整体封存；高频证据不逐条入 PostgreSQL |
| matching/{alignment_run_id}/matches.json | attempt_id、score_version、performance_hash、config、matcher_version、alignment_context、raw_matches | 对齐阶段审计输出 |
| alignment/{alignment_run_id}/result.json | 下面列出的 Result v3 | Worker create-only；READY引用和digest核验后才可导出 |
| events/* | Pub/Sub 归档消息；payload包含event_id/event_type及对应消息字段 | 归档订阅；可选 Spark 分析 |
| datasets/{新版本前缀}/* | 研究数据、上下文快照和提交文件 | Export create-only；manifest最后写 |

侧车 `events[]` 的全部字段：`sequence`、`type`（note_on/note_off）、`midiEventTimestamp`（ms）、`playerSongTime`（s）、`performanceNowTimestamp`（ms）、`pitch`（0..127）、`velocity`（0..127）、`timestampSource`（web_midi/browser_keyboard）。

`transportSegments[]`：`sequence`、`type`（start/resume/pause/stop）、`playerSongTime`、`performanceNowTimestamp`。这是录音时间轴证据，区别于 PostgreSQL 中的生命周期事件。

### Result v3 字段

顶层：`schema_version`、`alignment_run_id`、`attempt_id`、`score_version`（参考SHA-256）、`performance_hash`、`config`、`matcher_version`、`alignment_context`、`diagnostics`、`summary`、`raw_matches`、`notes`、`events_data`。

Worker 补充：`learner_key`、`song_id`、`session_id`（v2=attempt_id）、`source_environment`、`practice_metadata`（Upload metadata）、`attempt_created_at`、`alignment_completed_at`、`performance_events`、`transport_segments`、`performance_events_key`、`performance_events_hash`、`performance_event_audit_schema_version`、`alignment_quality_valid`、`alignment_quality_reason`。

- summary：expected_notes、correct_notes、extra_notes、missing_notes、accuracy、timing_labels_valid、alignment_quality_valid、alignment_quality_reason。
- alignment_context：time_basis、reference_origin_sec、observed_end_sec、observed_end_source、transport_segments_count、transport_audit_supplied、event_audit_supplied。
- events_data：kc_feature_names、onset_seconds、pitches、intervals_seconds、kc_vectors、reject_reason（event/note）、timing_offset_ms、pitch_offset_semitones、extra_notes；数组按参考 onset 对齐，手部维度 left/right。
- notes/raw_matches/diagnostics：匹配器版本化审计结构，包括匹配类型、参考/演奏音高与 onset、时序偏差、拒绝原因、quality和校准证据。并非稳定的在线 SQL 列；逐音符物化字段见第 7 节，原始诊断由 `alignment/service.py` 定义。
- config：AlignmentConfig.identity 的完整参数，见 `contracts.py`。改变配置/Matcher 会改变 run 身份。

原始遥测仍同时位于侧车与 Result；本轮没有删数组。文件引用与 JSON 字段的存在不等于 READY，必须以 PostgreSQL 提交指针为准。

## 6. 导出文件与边界

`export-attempts` 默认 `--source direct --environment production`，要求 cutoff 和新的 output-prefix。

| 文件 | 字段 / 含义 |
|---|---|
| attempts.jsonl | user_id（learner_key）、song_id、exercise_id、days_since_signup、song_type、visit_index、attempt_index、play_mode、chords_evaluated/chords_successful、notes_evaluated/notes_successful、time_playing、is_played_in_full、exit_status、exercise_part_index、kc_labeler_version、events_data |
| visits.json | Visit快照（移除真实user_id/learner_key映射后，用learner_key写user_id），加visit_index |
| attempt_metadata.json | Attempt快照、time_playing/is_played_in_full/exit_status/completion_reason/client_success，加attempt_index；移除registered_at和真实身份 |
| attempt_events.json | 已筛选Attempt的事件快照，移除真实user_id |
| index_map.json | user_id（learner_key）、visit_index、attempt_index、attempt_id、visit_id、alignment_run_id、score_version、matcher_version、feature_version、result_digest |
| intent.json / manifest.json | 同一发布计划：dataset_version、schema_version、source_environment、outcome_semantics、cutoff、count、skipped、source、feature_version、quality_policy、ordering、context_files、context_semantics、files（逐文件SHA-256）、publication_protocol |

只有 manifest 存在且逐文件摘要验证通过才算已发布。重复写同前缀仅允许完全相同计划；内容变化需新前缀。

`export-training` 单文件字段：source_environment、source、feature_version、quality_policy、runs（attempt_id/alignment_run_id/matcher_version/result_digest）、dataset_version、cutoff、examples、warning。每个 example：learner_key、attempt_id、alignment_run_id、event_index、onset_seconds、pitches、kc_vector、correct、reject_reason、ordering_timestamp、ordering_source。

环境先于索引分配筛选；质量门后筛选，不改变同环境真实演奏次数。unknown 不进入导出。固定 Cutoff 不代表可变数据库历史时光回溯；保留已发布的快照文件以复现。

## 7. 可选 GCS lake / checkpoint

`lake` 是 Delta 表目录（Parquet + _delta_log），只有启用 pipeline 才创建/更新；在线保存和默认 Direct Export 不依赖它。`checkpoint` 保存 Spark 的提交 offset 等运行状态，不是学习事实表。

下列字段清单由当前 `pipeline/tables.py` schema 生成。历史 Delta 表可能保留旧列；读取 Silver/Gold 必须经过 ops 提交集过滤。原始 Result/DB 中的 source_environment 是导出环境筛选依据；旧 Lake 物化列并未升级为独立的来源隔离系统，生产/开发应使用不同桶或 lake root。

### bronze/alignment_runs；bronze/score_notes；bronze/performance_notes；quality/alignment_runs；ops/completed_runs；ops/failures

`event_id` (string)、`event_type` (string)、`source_key` (string)、`payload_json` (string)。

### bronze/midi_events

`midi_event_id` (string)、`source_event_id` (string)、`alignment_run_id` (string)、`attempt_id` (string)、`learner_key` (string)、`song_id` (string)、`session_id` (string)、`event_type` (string)、`timestamp_source` (string)、`transport_segment_id` (string)、`source_key` (string)、`payload_json` (string)、`midi_event_timestamp_ms` (double)、`player_song_time_sec` (double)、`performance_now_timestamp_ms` (double)、`pitch` (int32)、`velocity` (int32)、`sequence` (int64)。

### bronze/transport_segments

`transport_segment_id` (string)、`alignment_run_id` (string)、`attempt_id` (string)、`learner_key` (string)、`song_id` (string)、`session_id` (string)、`transport_event_type` (string)、`source_key` (string)、`payload_json` (string)、`player_song_time_sec` (double)、`performance_now_timestamp_ms` (double)、`start_player_song_time_sec` (double)、`end_player_song_time_sec` (double)、`start_performance_now_timestamp_ms` (double)、`end_performance_now_timestamp_ms` (double)、`sequence` (int64)。

### silver/midi_alignment_events

`alignment_event_id` (string)、`alignment_run_id` (string)、`attempt_id` (string)、`learner_key` (string)、`song_id` (string)、`session_id` (string)、`score_version` (string)、`performance_hash` (string)、`alignment_version` (string)、`matcher_version` (string)、`alignment_type` (string)、`ref_id` (string)、`performance_id` (string)、`attempt_created_at` (string)、`attempt_created_at_jst` (string)、`attempt_date_jst` (string)、`alignment_completed_at` (string)、`alignment_completed_at_jst` (string)、`alignment_completed_date_jst` (string)、`alignment_quality_reason` (string)、`ref_pitch` (double)、`performance_pitch` (double)、`ref_onset_sec` (double)、`performance_onset_sec` (double)、`performance_player_song_time_sec` (double)、`performance_onset_sec_global_aligned` (double)、`timing_deviation_global_aligned` (double)、`raw_timing_deviation_sec` (double)、`calibration_offset_sec` (double)、`local_timing_deviation_sec` (double)、`timing_deviation_sec` (double)、`reject_reason` (int32)、`is_correct` (bool)、`within_observed_range` (bool)、`timing_labels_valid` (bool)、`alignment_quality_valid` (bool)、`source_key` (string)、`payload_json` (string)。

### gold/fact_midi_alignment

`alignment_event_id` (string)、`alignment_run_id` (string)、`attempt_id` (string)、`learner_key` (string)、`song_id` (string)、`session_id` (string)、`score_version` (string)、`performance_hash` (string)、`alignment_version` (string)、`matcher_version` (string)、`alignment_type` (string)、`ref_id` (string)、`performance_id` (string)、`attempt_created_at` (string)、`attempt_created_at_jst` (string)、`attempt_date_jst` (string)、`alignment_completed_at` (string)、`alignment_completed_at_jst` (string)、`alignment_completed_date_jst` (string)、`alignment_quality_reason` (string)、`ref_pitch` (double)、`performance_pitch` (double)、`ref_onset_sec` (double)、`performance_onset_sec` (double)、`performance_player_song_time_sec` (double)、`performance_onset_sec_global_aligned` (double)、`timing_deviation_global_aligned` (double)、`raw_timing_deviation_sec` (double)、`calibration_offset_sec` (double)、`local_timing_deviation_sec` (double)、`timing_deviation_sec` (double)、`reject_reason` (int32)、`is_correct` (bool)、`within_observed_range` (bool)、`timing_labels_valid` (bool)、`alignment_quality_valid` (bool)。

### gold/attempt_events

`event_id` (string)、`alignment_run_id` (string)、`attempt_id` (string)、`learner_key` (string)、`song_id` (string)、`session_id` (string)、`score_version` (string)、`event_index` (int32)、`onset_seconds` (double)、`pitches` (list<item: list<item: int32>>)、`intervals_seconds` (list<item: double>)、`kc_vector_json` (string)、`reject_reason_event` (int32)、`reject_reason_note_json` (string)、`timing_offset_ms_json` (string)、`pitch_offset_semitones_json` (string)、`extra_notes` (list<item: int32>)。

### gold/fact_alignment_attempt

`alignment_run_id` (string)、`attempt_id` (string)、`learner_key` (string)、`song_id` (string)、`session_id` (string)、`score_version` (string)、`alignment_version` (string)、`matcher_version` (string)、`attempt_created_at` (string)、`attempt_created_at_jst` (string)、`attempt_date_jst` (string)、`alignment_completed_at` (string)、`alignment_completed_at_jst` (string)、`alignment_completed_date_jst` (string)、`alignment_quality_reason` (string)、`time_basis` (string)、`expected_notes` (int64)、`correct_notes` (int64)、`extra_notes` (int64)、`missing_notes` (int64)、`anchors_total` (int64)、`anchors_kept` (int64)、`accuracy` (double)、`global_offset_sec` (double)、`offset_mad_sec` (double)、`reference_time_origin_sec` (double)、`observed_end_sec` (double)、`timing_labels_valid` (bool)、`alignment_quality_valid` (bool)。

### gold/fact_practice_attempt（历史投影）

`alignment_run_id` (string)、`attempt_id` (string)、`learner_key` (string)、`song_id` (string)、`score_version` (string)、`alignment_version` (string)、`expected_notes` (int64)、`correct_notes` (int64)、`extra_notes` (int64)、`missing_notes` (int64)、`accuracy` (double)、`timing_labels_valid` (bool)。

### Spark 事件表

`bronze/practice_events`、`quality/practice_events`、`silver/practice_events`、`ops/practice_event_commits`：`event_id string`、`payload_json string`。名字沿用历史命名，输入实际是已归档的消息；新 Challenge 已停止发送 practice.event。
