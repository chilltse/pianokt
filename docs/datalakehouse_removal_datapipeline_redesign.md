# PianoKT Data Architecture V3 — Complete Lakehouse Removal and Data Pipeline Redesign

## 1. 总体任务

在 PianoKT 完整实施一次数据架构重构。


彻底移除原有 Apache Spark、Delta Lake、Lakehouse、定时数据物化任务及相关遗留工程实现，替换为一套轻量化、事件驱动、版本化、可重现的 Raw–Bronze–Silver–Gold 数据架构。

本次允许 Breaking Changes。

我不需要保留旧 Lakehouse 的运行能力，不需要迁移旧 Bronze/Silver/Gold Delta Tables，不需要回填历史 Alignment Run，不需要维持旧数据导出路径的兼容性，也不需要进行长期的新旧架构双轨运行。

直接以新版架构作为唯一正式的数据处理架构。

但是：

- 不得误删原始 MIDI、学生账号、曲库、用户行为事实及其他核心业务数据。
- 可以不兼容旧 Alignment Result，但必须明确处理旧结果的状态及可见性。
- 不得将旧结果误认为新版已提交数据。
- 不得擅自执行生产数据库清空、GCS Bucket 删除或其他不可逆的云端操作。
- 本次需要完整实现代码、测试、数据库 Migration、Terraform 目标配置和部署操作手册，而不是仅仅生成设计文档。

## 2. 核心设计原则

整个项目遵循以下原则：

1. Single Source of Truth。
2. Event-driven processing。
3. Immutable raw evidence。
4. Versioned and reproducible transformations。
5. Explicit commit boundaries。
6. Deterministic dataset exports。
7. Minimal infrastructure。
8. Research reproducibility。
9. Environment isolation。
10. Clear data lifecycle management。

不为实现数据分层而引入额外服务、数据库或者计算引擎。

Raw、Bronze、Silver、Gold 表示数据处理成熟度，并不要求每一层独立部署或独立持久化。

最终架构只需要：

- Supabase / PostgreSQL。
- Google Cloud Storage。
- Cloud Run API。
- Cloud Run Worker。
- Pub/Sub。
- Outbox / Relay。
- 按需 Dataset Export Job。
- Python 数据处理模块。

不再需要 Spark、Delta Lake、Java、JAR 或 Lakehouse Pipeline。

## 3. 最终目标架构

```text
PianoKT Frontend
       |
       v
Raw Layer
  ├── GCS Raw Recordings
  └── Supabase Activity Facts
       |
       v
Outbox / PubSub / Worker
       |
       v
Bronze Layer
  ├── Input Validation
  ├── MIDI Parsing
  ├── Telemetry Parsing
  └── Time Normalization
       |
       v
Silver Layer
  ├── Reference Grouping
  ├── Performance Alignment
  ├── KC Feature Extraction
  ├── Correctness Labeling
  ├── Quality Assessment
  └── Immutable Result Publication
       |
       ├── Online Product Results
       ├── KT History / Inference
       └── On-demand Dataset Exporter
                    |
                    v
                Gold Layer
                  ├── Research Dataset
                  ├── Knowledge Tracing Dataset
                  ├── Recommendation Dataset
                  └── Learning Analytics Dataset
```

其中：

- Raw 持久化。
- Bronze 默认只在内存中计算。
- Silver 持久化。
- Gold 按需生成并持久化。
- 不存在定时 Lakehouse Materialization。
- 不存在为导出训练数据而运行的 Spark Job。
- 不存在 Delta Table 或 Spark Checkpoint。

## 4. 首先进行实际代码审计

在开始修改之前，完整检查当前分支中的：

- Backend Python。
- Frontend TypeScript。
- Supabase migrations。
- Terraform。
- Dockerfiles。
- requirements.lock。
- pyproject.toml。
- GitHub Actions。
- Makefile。
- Shell scripts。
- 本地开发工具。
- 数据集导出工具。
- PyKT integration。
- Recommendation pipeline。
- 曲库同步及 KC Extraction。
- 所有相关文档。

搜索以下遗留引用：

```text
spark
pyspark
delta-spark
deltalake
lakehouse
LAKE_ROOT
CHECKPOINT_ROOT
ENABLE_SPARK_EVENTS
spark_image
pipeline_schedule
lake_pipeline_paused
catch_up
publish_alignment
ops/completed_runs
--source lake
spark-events
```

同时检查：

- 所有 GCS Object Key 的生成逻辑。
- 所有硬编码 `midi/`、`matching/`、`alignment/`、`datasets/` 的位置。
- 所有读取 Result JSON 的代码。
- 所有使用 `piano_attempts.result_key` 的代码。
- 所有对旧 READY Result 的依赖。
- 所有与曲目 Song/Segment Identity 有关的代码。

不得只根据文件名称判断模块是否可以删除。

## 5. Raw Layer

### 5.1 定义

Raw Layer 是 PianoKT 的原始研究证据层。

保存实际发生的学生行为、演奏内容以及演奏时使用的参考数据。

Raw 数据不得依赖任何 Alignment、KC Labeler 或 KT 模型版本。

### 5.2 GCS Raw 数据

统一采用以下新版 Object Key：

```text
raw/
├── recordings/
│   └── {learner_key}/
│       └── {attempt_id}/
│           ├── reference.mid
│           ├── performance.mid
│           └── performance-events.json
│
└── events/
```

含义：

- `reference.mid`：学生练习时实际使用的 Reference MIDI 快照。
- `performance.mid`：学生真实演奏 MIDI。
- `performance-events.json`：原始按键遥测及 Transport 信息。
- `events/`：必要的系统事件归档。

继续使用现有 GCS Bucket，避免仅因为 Bucket 名称而重新创建或迁移整个 Bucket。

新版架构使用新的统一 Object Prefix。

不要求兼容旧 `midi/`、`matching/`、`alignment/`、`datasets/` 路径作为新版处理入口。

旧对象可以在独立命名空间内保留，等待管理员后续归档或删除。

### 5.3 Supabase Raw Facts

复用现有权威业务事实：

```text
student_visits
student_attempts
student_attempt_events
piano_attempts
piano_learner_keys
```

保存：

- Visit。
- Attempt。
- Attempt lifecycle。
- Started / Paused / Resumed / Finished / Exited。
- 原始时间戳。
- 实际演奏时长。
- MIDI Keyboard 使用状态。
- Waiting Mode。
- Left / Right Hand Settings。
- Assigned Range。
- Observed Range。
- Song ID。
- Segment ID。
- Segment Index。
- Song Version。
- MIDI Content Hash。
- Source Environment。

保留真实演奏事实与业务状态的区别。

Supabase 中的所有业务表不需要为了 Raw Layer 而重复复制到 GCS。

### 5.4 Raw 约束

必须保留：

- Create-only 对象写入。
- SHA-256 校验。
- GCS Generation 校验。
- 上传重试幂等性。
- Owner / Learner 隔离。
- Environment 隔离。
- Reference Snapshot 不可变。
- Attempt Identity 不可变。

曲库更新、曲目重新切分以及 KC Labeler 更新，不得修改历史演奏所使用的 Reference MIDI。

不可变性不代表永远不能删除数据。合规删除应通过独立、受控的数据生命周期流程完成。

## 6. Bronze Layer

### 6.1 定义

Bronze Layer 是原始 MIDI 和 Telemetry 的结构化解析层。

它不是数据库，也不是持久化数据仓库，而是 Python Worker 内部的可重现数据处理阶段。

### 6.2 职责

包括：

- MIDI Format Validation。
- MIDI Parsing。
- Note On / Note Off Parsing。
- Pitch / Velocity Parsing。
- Track Parsing。
- Transport Parsing。
- Timestamp Normalization。
- Time Unit Normalization。
- Basic Schema Validation。
- 输入文件完整性检查。

输出结构化的 Reference Notes、Performance Notes 和 Telemetry Records。

例如：

```python
ParsedAttempt(
    attempt_id=...,
    reference_notes=...,
    performance_notes=...,
    performance_events=...,
    transport_segments=...,
    metadata=...,
    provenance=...
)
```

上述结构仅为设计示意，优先复用现有类型和接口，不需要为了命名而重复创建一套对象模型。

### 6.3 实施要求

检查并复用：

```text
alignment/tracks.py
alignment/hands.py
alignment/service.py
upload_validation.py
upload_contracts.py
```

包括已有：

```python
read_midi_notes(...)
performance_notes_from_events(...)
```

合理划分基础解析与教育语义推断之间的职责。

手部推断、Reference Grouping、演奏匹配和学习标签属于后续 Silver 处理，不应混淆为单纯的 Raw Parsing。

不创建：

```text
Bronze Bucket
Bronze Database
Bronze Delta Table
Bronze Cloud Run Service
Bronze Scheduled Job
```

不重复解析同一个 MIDI。

如果现有 Alignment Service 已经具备合理的解析边界，则只进行必要的模块化整理，不进行无意义的拆分。

## 7. Silver Layer

### 7.1 定义

Silver Layer 是 PianoKT 唯一正式的标准化教育学习数据层。

Silver 负责将原始学生演奏转换为具有教育研究语义的数据。

### 7.2 处理过程

```text
Parsed Reference MIDI
+
Parsed Performance MIDI
+
Telemetry
       |
       v
Reference Grouping
       |
       v
Observed Range Resolution
       |
       v
Performance Alignment
       |
       v
Note-level Assessment
       |
       v
Onset-level Assessment
       |
       v
KC Feature Extraction
       |
       v
Quality Assessment
       |
       v
Silver Result
```

完整复用现有版本化实现：

```text
alignment/reference_grouping/
alignment/alignment_policies/
alignment/feature_extraction/
alignment/service.py
alignment/events.py
alignment/labels.py
alignment/serialization.py
alignment/time_guided.py
```

不修改当前 Alignment、Reference Grouping 和 KC Labeler 的数学定义。

不修改现有时间阈值、KC Vector 维度、Reject Reason、Extra Notes、Correctness Label 和正式评分语义。

### 7.3 Silver 物理目录

统一采用：

```text
silver/
└── alignment/
    └── {alignment_run_id}/
        ├── matches.json
        └── result.json
```

其中：

`matches.json` 保存必要的中间匹配审计结果。

`result.json` 保存唯一正式的标准化演奏结果。

不需要为 Note-level、Onset-level、Attempt-level 分别创建独立的物化数据库表。

保留可从 Result JSON 获得这些粒度数据的能力。

### 7.4 Silver Result Schema

原则上保留现有 Result Schema，避免破坏 PyKT 和下游数据消费者。

必须保证结果可以获取：

- Attempt ID。
- Learner Key。
- Song ID。
- Segment ID。
- Segment Index。
- Song Version / Content Hash。
- Reference Hash。
- Performance Hash。
- Telemetry Hash。
- Alignment Run ID。
- Alignment Version。
- Matcher Version。
- Reference Grouping Version。
- KC Labeler Version。
- KC Feature Names。
- Alignment Config。
- Alignment Context。
- Diagnostics。
- Summary。
- Note-level Results。
- Onset-level Events Data。
- Reject Reason。
- Extra Notes。
- Quality Status。
- Frontend Note Accuracy。
- Backend Event Accuracy。
- Source Environment。
- Schema Version。
- Data Architecture Version。

对于当前 Schema 已有的等价字段，直接复用，不要创建重复字段。

对于确实需要新增的字段，明确更新 Schema Version、序列化器、校验器和数据字典。

### 7.5 质量语义

区分：

```text
Successfully processed
Formally committed
Quality valid
Eligible for training
```

这些不是同一个概念。

一个 Alignment Result 可以处理成功并正式提交，但因为数据质量不合格而不进入训练数据集。

保留这类结果，支持后续诊断。

Gold 层负责决定哪些正式 Silver Results 可以被纳入具体研究数据集。

## 8. 建立全新的 Silver Run Registry

不需要回填旧历史 Run，但希望新版从第一天开始就具备完整版本管理能力。

新增轻量 PostgreSQL 表：

```text
piano_alignment_runs
```

参考字段：

```text
alignment_run_id
attempt_id
learner_key

song_id
segment_id
segment_index
song_version
midi_sha256

reference_hash
performance_hash
telemetry_hash

result_key
result_digest
result_schema_version

alignment_version
matcher_version
reference_grouping_version
kc_labeler_version
config_digest

source_environment
quality_valid
quality_reason

processing_purpose
committed_at
```

实际字段、类型、主键、外键和索引必须根据现有 Supabase Schema 设计，不允许直接照搬示意字段而制造重复数据。

### 8.1 Registry 职责

- 登记所有新版正式提交的 Alignment Run。
- 保留同一个 Attempt 的多个算法处理版本。
- 保存不可变数据血缘。
- 支持明确的历史 Run 选择。
- 支持研究重处理。
- 支持数据集复现。
- 不依赖任何 Delta `ops/completed_runs`。

### 8.2 当前线上结果

继续保留：

```text
piano_attempts.alignment_run_id
piano_attempts.result_key
piano_attempts.result_digest
piano_attempts.result_committed_at
```

它们表示当前在线业务使用的正式结果。

Run Registry 表示所有已正式提交的新版本结果。

当前业务指针与历史研究结果具有不同职责。

### 8.3 提交机制

提交过程必须满足：

```text
Worker generates Silver Result
       |
       v
Write immutable GCS objects
       |
       v
Validate result bytes and digest
       |
       v
Begin PostgreSQL transaction
       |
       ├── Insert Run Registry
       ├── Update current result pointer if applicable
       ├── Update official score if applicable
       └── Insert Outbox Event if applicable
       |
       v
Commit
```

保留现有 Worker Lease Fencing、事务保证和幂等性。

同一个 Run ID 与相同内容重复提交应当安全。

同一个 Run ID 对应不同内容必须拒绝。

数据库事务失败时，即使 GCS 已存在 Result 文件，也不得认为该 Result 已正式提交。

### 8.4 不进行历史回填

不从旧 Delta Tables 迁移 Run。

不迁移旧 `ops/completed_runs`。

不扫描旧 Result JSON 自动登记历史 Run。

不维护旧 Registry Adapter。

新版 Registry 从新架构正式启用时开始记录。

旧 Alignment Results 不进入新版 Gold Dataset，除非未来显式使用其 Raw 数据重新处理并完成新版正式提交。

## 9. Gold Layer

### 9.1 定义

Gold Layer 是面向具体研究任务和机器学习任务的版本化数据集。

Gold 不参与学生在线录音、评分或 Worker 的主处理事务。

只按需生成。

### 9.2 统一目录结构

```text
gold/
├── attempts/
│   └── {dataset_version}/
│       ├── attempts.jsonl
│       ├── visits.json
│       ├── attempt_events.json
│       ├── attempt_metadata.json
│       ├── index_map.json
│       ├── intent.json
│       └── manifest.json
│
├── knowledge_tracing/
│   └── {dataset_version}/
│       ├── training.json
│       ├── intent.json
│       └── manifest.json
│
├── recommendation/
│
└── learning_analytics/
```

本次需要完整实现 Attempt Dataset 和 Knowledge Tracing Dataset。

Recommendation Dataset 和 Learning Analytics Dataset 只保留合理的扩展边界。

不要创建没有真实功能的空模块、空服务或占位实现。

### 9.3 复用现有 Exporter

继续使用：

```text
pipeline/attempts.py
pipeline/training.py
pipeline/direct.py
pipeline/selection.py
pipeline/kc_versions.py
publication.py
```

但是需要将其彻底改造为基于 PostgreSQL + GCS Silver Results 的唯一读取链路。

新版导出不允许：

- 查询 Delta Tables。
- 扫描 GCS 猜测正式完成的 Run。
- 根据文件存在自动选择样本。
- 回退到 Lakehouse。
- 同一个 Attempt 默认重复纳入多个 Alignment Run。

### 9.4 Gold Selection

支持：

- Environment。
- Cutoff。
- Learner Selection。
- Song Selection。
- Segment Selection。
- Alignment Version。
- Reference Grouping Version。
- KC Labeler Version。
- Explicit Run Selection。
- Quality Policy。
- 数据排除策略。

不必为每一个过滤条件立即创建公开 CLI 参数，但内部选择契约应支持必要扩展。

默认只读取新版正式提交的 Silver Result。

默认每个 Attempt 只选择一个正式 Run。

如果没有指定研究版本，允许按照明确记录的当前正式业务 Run 选择规则导出。

如果指定 Alignment/KC 版本，则必须严格按照版本和 Run Registry 选择。

同一个 Attempt 在目标条件下对应多个 Run 且无法明确区分时，应报错而不是任意选择。

### 9.5 保留现有 KT 训练语义

必须保留当前：

```text
student
visit
attempt
onset event
```

的层级关系和顺序语义。

保留：

- KC Vector。
- Correct / Incorrect。
- Reject Reason。
- Pitches。
- Intervals。
- Timing Offset。
- Extra Notes。
- MIDI Keyboard Used。
- Song / Segment Identity。
- Attempt Metadata。
- Visit Metadata。

保留现有 UTC 排序和 Visit/Attempt 顺序规则。

当前训练正确性映射及 KC Encoding 不得改变。

本次不修改 PyKT 的词表、Question ID、PID 或模型输入定义。

### 9.6 Gold Manifest

所有新版 Gold Dataset 都必须具有明确的发布凭证。

Manifest 至少记录：

```text
dataset_version
dataset_type
schema_version
data_architecture_version

created_at
cutoff
source_environment

source_selection
alignment_versions
matcher_versions
reference_grouping_versions
kc_labeler_versions
quality_policy_version

ordering_policy
source_runs
source_snapshot_digest

student_count
visit_count
attempt_count
event_count

excluded_count
exclusion_reasons

files
file_sha256
publication_protocol
```

大型 Source Run 清单可以存储在独立文件中，由 Manifest 引用及校验，不必全部内嵌。

Dataset Version 应由确定性的来源集合、选择规则、版本语义和输出内容生成。

不要简单地用当前系统时间作为唯一 Dataset Version。

相同输入、相同规则和相同版本应产生确定性的数据身份。

生成时间等非确定性运行元数据不应破坏内容身份或幂等发布。

### 9.7 发布协议

复用现有：

```text
intent.json
    ↓
Immutable Data Files
    ↓
manifest.json
```

只有 Manifest 正式生成并且所有文件 Hash 校验通过，才视为有效的 Gold Dataset。

由于当前 `export-training` 是单文件导出，本次需要将其纳入统一 Gold Bundle 发布机制。

保留 `training.json` 内部现有消费格式，避免无关的 PyKT Loader 重构。

同步更新现有下载和训练脚本，使其通过新版 Manifest 找到正式训练文件。

不得发布部分成功的数据集。

不得覆盖已有的不同内容 Dataset Version。

## 10. 统一 Storage Layout

创建轻量统一的 Object Key 管理模块，例如：

```text
storage_layout.py
```

集中管理：

```python
raw_recording_key(...)
raw_reference_key(...)
raw_telemetry_key(...)
silver_matches_key(...)
silver_result_key(...)
gold_dataset_prefix(...)
gold_manifest_key(...)
```

避免在 Worker、API、Exporter、PyKT Integration 和测试代码中重复硬编码 GCS 路径。

统一定义数据架构版本，例如：

```python
DATA_ARCHITECTURE_VERSION = "3"
```

该版本用于明确区分旧架构和新版架构，不替代 Alignment Version 或 Dataset Schema Version。

必须同步检查和更新：

- Signed Upload URL。
- Signed Download URL。
- Reference MIDI 下载。
- Performance MIDI 下载。
- Result JSON 读取。
- Worker Result Key 验证。
- PyKT History Sync。
- Challenge History Details。
- 本地调试工具。
- Dataset Export。
- Terraform IAM Conditions。
- Pub/Sub Archive Prefix。
- Existing Catalog References。

曲库文件本身是独立的业务资产，不得误移动或改写其现有存储逻辑。

## 11. 彻底移除旧 Lakehouse 实现

### 11.1 删除候选文件

在审计确认没有独立用途后，删除：

```text
backend/Dockerfile.spark
backend/fetch-jars.sh

backend/pianokt_backend/pipeline/jobs.py
backend/pianokt_backend/pipeline/tables.py
backend/pianokt_backend/pipeline/spark_events.py

backend/tests/test_spark.py
```

如其中包含仍有价值的通用数据转换逻辑，只迁移真正有用的部分。

不得以换名的形式保留整套旧 Lakehouse Materializer。

### 11.2 Python Dependencies

从 `backend/pyproject.toml` 删除：

```text
deltalake
pyspark
delta-spark
```

检查 `pyarrow` 是否仍被其他模块或依赖使用。

如果没有实际用途，则删除。

重新生成：

```text
backend/requirements.lock
```

确保普通 Backend Docker Image 不再安装任何 Lakehouse 专用依赖。

保留 Alignment、MIDI Processing、NumPy、Pandas、FastAPI、PostgreSQL、GCS 和 Pub/Sub 所需依赖。

不允许为了替代 Spark 而引入新的大型数据处理框架。

### 11.3 CLI Cleanup

删除：

```bash
pianokt-data pipeline
pianokt-data spark-events
```

删除：

```text
--source lake
--source direct
--lake
--replay
```

中仅为旧 Lakehouse 服务的参数。

Direct 成为唯一正式的数据源，因此不再需要 source 切换参数。

移除：

```text
pianokt-lakehouse-v1
```

相关 advisory lock 和旧物化流程。

修复本地 `align` 命令，使其不再调用 `Tables` 或 `catch_up()`。

本地调试数据必须与正式研究数据隔离。

### 11.4 环境变量

删除：

```text
LAKE_ROOT
CHECKPOINT_ROOT
ENABLE_SPARK_EVENTS
```

统一引入明确的 GCS 数据根路径配置，例如：

```text
DATA_ROOT
```

同步更新 API、Worker、Relay、Exporter 和本地环境启动配置。

不保留旧 `RAW_ROOT` 作为长期双重配置来源；如果换名涉及部署，提供一次性切换说明。

所有数据环境仍需明确隔离。

### 11.5 CI / Makefile

删除 Spark 专用 CI Job。

删除 Java Setup。

删除 JAR 下载。

删除旧 Makefile Pipeline Target。

检查所有 GitHub Actions、Shell Script 和部署文档。

测试代码不得继续依赖 Delta Lake。

对于依赖旧 Lake Fixture 的测试，迁移至新版正式提交结果 Fixture。

不得为了使测试通过而直接删除数据完整性测试。

## 12. 新版 CLI 设计

保留统一 CLI：

```bash
pianokt-data
```

至少支持：

```bash
pianokt-data align
pianokt-data extract-reference
pianokt-data worker
pianokt-data relay
pianokt-data import-song
pianokt-data sync-catalog
pianokt-data rebuild-projections
pianokt-data export-attempts
pianokt-data export-training
```

统一新版 Export 参数。

例如：

```bash
pianokt-data export-attempts \
  --environment development \
  --cutoff 2026-12-01T00:00:00Z
```

以及：

```bash
pianokt-data export-training \
  --environment development \
  --cutoff 2026-12-01T00:00:00Z
```

默认根据 Dataset Type 和确定性 Dataset Version 自动生成 Gold Prefix。

支持显式输出路径时，必须验证该路径属于正确的数据命名空间。

支持明确的：

```text
--run-selection
--kc-labeler-version
--alignment-version
```

等必要版本选择参数。

最终参数名称需要与实际实现保持一致。

更新 CLI Help、README、部署脚本和数据导出文档。

## 13. 新增历史数据重新处理机制

旧 Silver Result 不需要迁移，但新版架构必须具备未来从 Raw 重新生成 Silver 的能力。

新增建议命令：

```bash
pianokt-data reprocess
```

例如：

```bash
pianokt-data reprocess \
  --environment development \
  --attempt-id ATTEMPT_UUID \
  --alignment-version 1.0.0 \
  --kc-labeler-version 1.2.0 \
  --dry-run
```

支持：

- 指定单个 Attempt。
- 指定明确的 Attempt 集合。
- 指定版本。
- Dry Run。
- 可重试。
- 幂等性。
- 失败报告。
- 处理进度统计。

复用正常 Worker 的解析、Alignment、KC Labeler 和 Silver Publication 逻辑，不允许重新实现一套研究专用 Alignment 算法。

默认重新处理是研究用途。

默认只生成新的正式 Silver Run，不修改学生现有在线评分，不替换当前业务 READY 指针，不向 PyKT Serving 自动同步替代历史。

如果需要推广新 Run 为正式业务结果，应具有独立且显式的 Promote 操作，并检查在线 KT History 是否需要重建。

不自动批量重新处理旧数据。

新版首次上线不要求重新处理所有旧 Attempt。

## 14. 曲库与数据架构的协同

检查当前：

```text
song_identity.py
song_import.py
catalog_sync.py
pykt_catalog.py
pykt_candidate_cache.py
```

确保与新版数据架构一致。

曲库继续作为独立的 Reference Data Domain。

逻辑上区分：

```text
Song Catalog
    ├── Entire Song
    ├── Segments
    ├── Song Versions
    └── MIDI Files
```

与：

```text
Student Learning Data
    ├── Raw
    ├── Bronze
    ├── Silver
    └── Gold
```

完整原曲和 Segment 都应该具有明确身份。

在不改变现有产品定义的前提下：

- Challenge 以 Segment 为主要练习单位。
- Practice 支持现有完整曲目及分段练习能力。
- 所有研究记录使用稳定的 Song/Segment Identity。
- 曲目重新切分后不能让旧 Attempt 指向错误的 Segment。
- 不通过曲目名称匹配数据身份。
- 不通过 Part 显示名称判断数据身份。

Standalone Reference Extraction 继续使用当前版本化的 KC Labeler 和 Reference Grouping。

候选曲目的 Reference Features 与学生真实演奏的 Silver Results 必须区分。

推荐候选曲目的 KC Feature Cache 可独立版本化并重建，但不应被误认为学生训练标签。

## 15. PyKT Integration Compatibility

重点检查：

```text
pykt_history.py
pykt_sync.py
pykt_integration.py
pykt_runtime.py
pykt_recommendation.py
pykt_candidate_cache.py
```

确保它们读取新版正式提交的 Silver Results。

在线 KT History 和 Gold KT Training Dataset 应共享基础数据校验与标签语义。

但是：

- 在线 KT History 默认读取当前正式业务结果。
- Gold Dataset 可以显式选择历史研究版本。
- 不能把尚未生效的研究 Run 自动加入在线历史。
- 不能将同一 Attempt 的不同 Run 重复同步至模型。
- 不修改模型词表、模型权重、训练目标或推荐策略。

如果新版架构改变了已有 KT History 的来源集合，应明确拒绝不一致的继续增量同步，并提供受控重建或新模型状态命名空间方案。

不得静默覆盖旧模型状态。

## 16. Supabase Migration

新增正式 Migration，编号以当前分支实际最后一个 Migration 为准。

用于：

- 创建新版 Alignment Run Registry。
- 增加必要的数据架构版本字段。
- 建立索引、约束和必要权限。
- 管理当前正式 Result Pointer 与 Registry 的关联。
- 区分旧数据与新版已提交数据。
- 保证正式业务查询和 Gold Export 的正确过滤。
- 支持未来研究重处理。

不删除现有历史 Migration。

特别是：

```text
009_lakehouse_backend.sql
```

虽然名称中包含 Lakehouse，但其中建立了核心业务表，不能删除。

不要因为旧架构名称而误删：

```text
piano_attempts
piano_outbox
piano_learner_keys
student_visits
student_attempts
student_attempt_events
challenge_recordings
user_play_logs
```

不需要对旧 Alignment Results 做兼容性回填。

对于旧 READY 数据，可以采用明确的 Legacy Data Architecture Version 标记。

旧结果不得进入新版 Gold Export。

必须定义旧 Challenge History 和正式评分的可见性策略。

不应通过粗暴地清空所有 Attempt 或将其全部标记为 FAILED 来完成切换。

## 17. Terraform Infrastructure Cleanup

彻底移除旧 Lakehouse 运行基础设施。

目标退役资源：

```text
pianokt-pipeline Cloud Run Job
pianokt-pipeline Cloud Scheduler
pianokt-pipeline Service Account

Pipeline-specific IAM Bindings
Lake Bucket
Spark Checkpoint Bucket
Spark Image Configuration
```

删除以下 Terraform Variables 和配置：

```text
spark_image
lake_pipeline_paused
pipeline_schedule
```

清理 Relay Job 中无用的 Lakehouse 环境变量。

保留：

```text
pianokt-api
pianokt-worker
pianokt-relay
pianokt-export

pianokt-events
pianokt-dead-letter
Worker Pub/Sub Subscription

Supabase
GCS Data Bucket
Terraform State Bucket
Artifact Registry
```

Pub/Sub Archive 如果仍然承担研究事件记录或审计职责，则保留，并使用新版 Raw Event Prefix。

`pianokt-export` 使用普通 Backend Docker Image。

不包含 Java、Spark 或其他大型处理引擎。

Exporter 只按需执行，不创建定时 Dataset Export Scheduler。

### IAM

根据新版路径更新最小权限：

- API：读取及创建正常业务所需对象。
- Worker：读取 Raw、创建 Silver。
- Exporter：读取正式 Silver、创建 Gold。
- Pub/Sub Archive：写入指定 Raw Event Prefix。
- 不向普通运行服务授予不必要的删除权限。

具体 IAM Conditions 必须与实际 GCS 权限和现有 Signed URL 机制一致。

### Terraform 安全要求

保持 API、Worker、Relay 和共享资源 Terraform 地址稳定。

正确处理：

```text
deletion_protection
force_destroy
Terraform State
Non-empty Buckets
```

不能因删除 `for_each` 中的 Pipeline 元素而误重建 Relay。

不允许自动删除 Terraform State Bucket。

输出真实 Terraform Plan 或说明无法取得真实 Plan 的原因。

旧 Lake 和 Checkpoint Bucket 不再作为新版运行架构的一部分，但实际删除对象及 Bucket 必须由管理员显式批准。

## 18. Handling Legacy Data

本次明确不要求：

- 迁移旧 Delta Tables。
- 迁移旧 Bronze/Silver/Gold 物化结果。
- 回填旧 Alignment Run。
- 保留 `--source lake`。
- 保留旧 Spark Job。
- 保留旧 Lakehouse 读取 Adapter。
- 维持长期双轨运行。
- 对所有旧学生数据重新执行 Alignment。

允许直接使用新版命名空间开始收集和处理数据。

旧 Raw 数据、用户行为事实和核心曲库默认保留。

旧 Silver 和 Gold 数据可以归档或在独立批准后删除。

切换之前，需要明确处理尚处于上传、ACCEPTED、VALIDATING、PROCESSING 或 RETRY 状态的旧 Attempt。

应选择受控完成、显式取消或其他经过验证的切换策略。

不得让旧 Worker 和新 Worker 使用不同 Object Key 契约并竞争处理同一个 Attempt。

旧数据不兼容新版时，应明确隔离并报告，而不是为此重新引入长期兼容层。

## 19. Data Lifecycle Management

新版架构必须为以后以下操作提供明确边界：

```text
Unpublish Song
Exclude Song from Research
Delete Song
Delete Segment
Delete Student Attempt
Delete Student Data
Reprocess Raw Data
Withdraw Dataset
Rebuild Dataset
```

区分曲目下架和彻底删除。

曲目下架默认仅影响新的产品展示、练习和推荐。

不得默认删除所有历史练习事实。

研究排除策略必须作用于后续 Gold Export。

需要彻底删除学生数据时，应覆盖：

- Supabase。
- GCS Raw。
- Silver Results。
- Run Registry。
- Gold Dataset 受影响范围。
- KT History / Model State。
- 其他相关派生数据。

不得通过 Reprocess 或 Dataset Rebuild 将明确删除的数据重新引入。

对于已发布 Gold Dataset，应有明确的撤回、废弃或重新发布机制。

本次重点建立必要的身份、引用关系和排除机制，不需要引入完整的数据治理平台。

## 20. Testing and Validation

必须执行真实测试，不允许只进行代码修改和文档说明。

### 20.1 Architecture Tests

验证普通 Python 环境不依赖：

```text
Spark
Delta Lake
Java
Spark JAR
```

验证：

- CLI Help 不存在旧 Lakehouse 命令。
- Backend Docker Image 可以正常构建。
- 不需要 Lake Bucket 或 Checkpoint Bucket 即可处理完整 Attempt。
- 不需要独立 Pipeline Job 即可导出 Gold Dataset。

### 20.2 End-to-End Tests

使用测试环境或本地完整集成环境执行：

```text
Create Visit
    ↓
Create Attempt
    ↓
Upload Reference MIDI
    ↓
Upload Performance MIDI
    ↓
Upload Telemetry
    ↓
Finalize
    ↓
Worker
    ↓
Bronze Parsing
    ↓
Silver Alignment
    ↓
Run Registry Commit
    ↓
READY
    ↓
Gold Attempt Export
    ↓
Gold KT Export
    ↓
Manifest Verification
```

使用真实可解析的 MIDI Fixture。

不能只用 Mock JSON 代替完整 MIDI 处理过程。

### 20.3 Semantic Regression Tests

不需要迁移旧历史数据，但必须使用同一份测试输入验证重构前后算法语义不变。

比较：

- Reference Onset Grouping。
- Alignment Result。
- KC Vector。
- Reject Reason。
- Correctness Label。
- Extra Notes。
- Frontend Note Accuracy。
- Backend Event Accuracy。
- Event Ordering。
- Song ID。
- Segment ID。
- Quality Assessment。

新旧 Storage Path 不同不应导致教育语义变化。

### 20.4 Data Integrity Tests

至少覆盖：

- Duplicate Upload。
- Duplicate Worker Delivery。
- Duplicate Run Commit。
- Conflicting Run Identity。
- Missing Raw MIDI。
- Missing Silver Result。
- Invalid Result Digest。
- Database Commit Failure。
- Expired Worker Lease。
- Cross-learner Data。
- Cross-environment Data。
- Invalid Song/Segment Identity。
- Mixed incompatible KC Versions。
- Multiple eligible Runs。
- Dataset publication interruption。
- Manifest Hash Mismatch。
- Retry after partial publication。
- Legacy Result Exclusion。
- Deleted/Excluded Data Reintroduction Prevention。

### 20.5 PyKT Tests

验证：

- 当前 READY Result 可以构建 PyKT History。
- 新版 Silver Result 可以进入现有模型输入转换。
- Dataset Schema 与训练消费者兼容。
- KC Labeler Version 严格验证。
- 不重复同步相同 Attempt。
- 研究重处理不自动污染在线 KT State。
- Recommendation Source Binding 保持有效。

有真实模型或训练环境时，执行真实 Loader 和小规模 Smoke Test。

如果缺少模型权重或外部训练仓库，明确报告限制，不能用 JSON 可解析代替真实模型验收。

### 20.6 Infrastructure Tests

执行：

```bash
terraform fmt
terraform validate
```

在具备授权及配置的情况下执行：

```bash
terraform plan
```

检查是否意外删除或重建任何保留资源。

执行 Backend Test Suite、必要 Frontend Test Suite、Type Check、Build 和 Docker Build。

修复由本次重构引起的真实回归。

不得通过关闭测试、增加任意 Skip 或降低校验严格性来获得表面通过。

## 21. Documentation

统一重写并更新当前正式文档：

```text
README.md

docs/ARCHITECTURE_ZH.md
docs/DATA_CONTRACT_V2_ZH.md
docs/DEPLOYMENT_AND_ROADMAP_ZH.md
docs/LOCAL_DEVELOPMENT_ZH.md
docs/PIANOKT_SYSTEM_REFERENCE_ZH.md
docs/VISITS_AND_ATTEMPT_DATASET_ZH.md
docs/PYKT_INTEGRATION.md
docs/PYKT_RECOMMENDATION.md
```

根据实际情况重命名或建立新版文件，例如：

```text
docs/DATA_ARCHITECTURE_V3.md
docs/DATA_CONTRACT_V3.md
docs/DATASET_EXPORT_GUIDE.md
docs/DATA_LIFECYCLE.md
docs/DATA_ARCHITECTURE_V3_CUTOVER.md
```

避免保留多个互相冲突的正式架构文档。

旧 Spark/Lakehouse 实施文档可以删除或明确归档为历史设计，不再作为当前运行指南。

最终文档必须准确说明：

- Raw Layer。
- Bronze Layer。
- Silver Layer。
- Gold Layer。
- Storage Layout。
- Run Registry。
- Dataset Manifest。
- Version Management。
- Data Lineage。
- Reprocessing。
- Dataset Export。
- Data Lifecycle。
- Deployment。
- Clean Cutover。
- Testing。

文档与实际代码保持一致，不得描述尚未实现的功能为正式能力。

## 22. 最终完成标准

最终工程必须能够在完全不存在 Spark、Delta Lake、Lake Bucket 和 Checkpoint Bucket 的情况下运行。

必须满足：

1. 学生能够正常练习及上传 MIDI。
2. 原始数据进入新版 Raw。
3. Worker 内存中完成 Bronze 解析。
4. Worker 生成并提交版本化 Silver Result。
5. PostgreSQL 保存正式 Run Registry。
6. 前端能够正常显示正式评分。
7. PyKT 能够读取当前正式学习结果。
8. 可按需导出 Gold Attempt Dataset。
9. 可按需导出 Gold KT Dataset。
10. Gold Dataset 具有 Manifest 和完整数据血缘。
11. 可以从 Raw 重新生成指定版本的 Silver。
12. 不需要运行任何 Lakehouse Pipeline。
13. 不存在旧 Lakehouse 运行依赖和兼容分支。
14. 不修改现有研究算法的数学语义。
15. 新版环境不会混入未验证的旧结果。

## 23. Implementation Strategy

本次是一次完整重构任务，不要只完成第一阶段后停止。

建议在同一分支内按以下顺序完成所有实现：

1. Repository Audit。
2. Storage Layout。
3. Raw / Bronze / Silver 处理边界。
4. Silver Run Registry。
5. Worker Commit Refactor。
6. Direct-only Export。
7. Gold Publication。
8. Reprocess CLI。
9. PyKT Integration Compatibility。
10. Legacy Lakehouse Removal。
11. Dependency Cleanup。
12. Terraform Cleanup。
13. Tests。
14. Documentation。
15. Cutover Instructions。

允许将多个逻辑步骤拆成独立 Commit，以便审查和回滚。

不需要新旧架构长期共存。

不要因为历史兼容性要求而保留旧 Spark 或 Delta 实现。

完成所有可在当前开发环境执行的工作。

如果生产部署权限、数据库凭证或云端访问不可用，仍需完整实现代码、Migration、Terraform 和本地测试，并给出精确部署手顺。

不擅自执行破坏性生产操作。

## 24. Final Report

完成之后提供一份详细实施报告，包含：

- 实际读取的 Branch 和 Commit。
- 修改前架构。
- 修改后架构。
- 新版完整数据流。
- 文件新增、修改和删除清单。
- Python Dependency Changes。
- Database Migration Details。
- GCS Object Layout。
- Run Registry Schema。
- Gold Dataset Schema。
- CLI Usage。
- Terraform Resource Changes。
- Test Results。
- Actual End-to-End Validation Results。
- Known Limitations。
- Clean Cutover Procedure。
- Cloud Resources Retirement Procedure。

明确区分：

```text
Implemented
Locally Verified
Real Environment Verified
Deployed
Not Verified
```

不要将本地实现误报为生产部署完成。

最终目标不是创建一个新的 Lakehouse，而是让 PianoKT 的数据工程尽可能简单、可靠、模块化、版本化，并服务于真实的钢琴学习研究和推荐系统。