# PianoKT 当前实现参考

更新日期：2026-11-04。依据当前工作区源码和 migration 001–016 整理。本文件说明“代码实际如何连接”，运行与部署步骤见 [部署手册](DEPLOYMENT_AND_ROADMAP_ZH.md)。先理解整体可读 [架构总览](ARCHITECTURE_ZH.md)。

当前主路径为 Challenge v2 单表事件权威 + Direct Export。完整数据库字段、GCS 对象和数据流以 [v2 数据字典](DATA_CONTRACT_V2_ZH.md) 和 [架构总览](ARCHITECTURE_ZH.md) 为准。下面保留算法、模块和可选 Lake 的实现参考。升级需先执行 migration 016，再协调发布 API/Worker/前端；旧上传契约不自动转换。

## 1. 系统边界

工程包含一个浏览器练习应用、一组 Python 后端程序、一套 Supabase schema，以及 GCP 基础设施配置。

前端是 React/TypeScript + React Router；全局播放器与部分应用状态采用 Jotai，曲目读取采用 SWR。后端在线接口采用 FastAPI，数据库连接采用 psycopg；同一 Python 包也提供 worker、relay、pipeline 和数据导出命令。

服务拆为在线 API、私有 worker、定时 relay/pipeline，代码仍共用 `backend/pianokt_backend`。它们没有各自独立的一套业务模型，也没有当前正在运行的训练服务。

Supabase 用于身份、资料、RLS 和在线 PostgreSQL 表；新的 Challenge 文件直传私有 GCS。原 Supabase Storage 录音路径保留读取兼容，头像仍使用 avatars 桶。

## 2. 目录与职责

```text
src/
├─ pages/                         页面、路由与组件编排
│  ├─ providers.tsx               AuthProvider、PlayerProvider 等
│  ├─ challenge/page.tsx          Challenge 的界面、录音与终止流程
│  ├─ challenge/useChallengeAttempt.ts  活跃时长、事件、终止状态
│  ├─ challenge/LocalAlignmentTimeline.tsx  本地对齐调试图
│  └─ recordings/page.tsx         录音列表、MIDI 下载、推荐入口
├─ features/
│  ├─ auth/                       Supabase 登录、资料与访问控制
│  ├─ data/、parsers/              曲目加载与解析
│  ├─ player/                     乐谱时间、播放、暂停、倒数、即时评分
│  ├─ midi/                       输入时钟与分段录音
│  ├─ SongVisualization/、synth/   显示与声音
│  ├─ persist/                    本地曲目句柄和练习设置持久化
│  ├─ activity/                   visit 生命周期与活动事件队列
│  └─ challenge-history/          上传队列、参考快照、查询、推荐
backend/pianokt_backend/
├─ api.py                         浏览器业务 HTTP 入口
├─ upload_contracts.py            上传、telemetry、完整 Activity 模型与校验
├─ upload_service.py              最终提交的事务内业务写入
├─ activity.py                    visit/attempt/event 的幂等事务写入
├─ projections.py                 产品投影生成/重建
├─ provenance.py                  服务端环境与导出来源校验
├─ online.py                      PostgreSQL 连接与归属查询
├─ storage.py                     本地/GCS create-only 对象访问
├─ publication.py                 多文件数据集提交与摘要校验
├─ worker_api.py                  私有 Pub/Sub push 入口
├─ worker.py                      对齐任务、租约、正式成绩、relay
├─ alignment/                     匹配、手部、音符标签与 onset 特征
├─ pipeline/                      事件流、Delta 表、导出
├─ inference.py                   推荐接口与演示实现
└─ cli.py                         命令行入口 pianokt-data
supabase/                         migrations、后端权限组、演示目录
infra/gcp/                        Terraform：IAM、桶、服务、队列、调度
```

## 3. 前端练习链路

### 3.1 曲目和播放器

`features/data/index.tsx:useSong` 按 source 读取：builtin 对应 `/music/songs/<id>`；local 通过持久化文件句柄读取；base64 在浏览器中解析。它们转换为统一 Song，供播放器和可视化使用。仓库也有 MusicXML 解析工具，不能据此推断当前 useSong 的所有入口都在加载 MusicXML。

`player/player.ts` 管理 CannotPlay、CountingDown、Playing、Paused。歌曲位置、速度、片段、左右手、等待模式、节拍器和移调由这里及 SongConfig 协作实现。即时 perfect/good/missed/error 与 accuracy 服务于界面反馈，和后端正式对齐评分是不同计算过程。

### 3.2 三种时间

- UTC 时间戳：访问和演奏发生的真实日期时间，供历史顺序与 cutoff 使用。
- player song time：曲谱播放到第几秒，供录音和参考对齐使用。
- 浏览器 monotonic 时间：MIDI event timestamp / performance.now，供精确采集与 transport 审计使用。

暂停时现实时间继续流逝，歌曲时间停止。不能用两个 UTC 时间戳的差直接代表有效弹奏时长，也不能把 performance.now 当成 UTC 日期。

`midi/index.tsx` 为输入保留 `midiEventTimestamp`、`performanceNowTimestamp`、`timestampSource`。`useSegmentedRecordMidi.ts` 使用播放器的歌曲时间采样，保存 note-on/off 和 start/resume/pause/stop 片段。

### 3.3 参考快照

`challenge-history/referenceSnapshot.ts` 根据指定手部、移调和范围重新生成参考 MIDI。Challenge 在开始前冻结 assignment；结束时按实际 observed range 裁剪快照。

- `assigned_range` 是原本分配的练习范围。
- `observed_range` 是实际练习到的范围。
- `reference_time_origin_sec` 把绝对歌曲时间转换为快照内时间。

提前退出会改变实际参考快照摘要，因此 score_version 是“本次参考输入的版本”，不能单独当成整首歌或练习身份。exporter 另用歌曲与 assigned settings 生成 exercise_id。

## 4. 访问与演奏的身份模型

### 4.1 身份和顺序字段

- Auth user ID：登录学生的在线身份，只留在受控业务表中。
- learner_key：在线身份对应的稳定伪名，供 raw/分析/导出关联。
- visit ID：一个标签页的网站访问窗口。
- attempt ID：一次实际演奏；新 Challenge 也用它作为旧播放周期 session_id。
- event ID：一个不可变活动或播放事件。
- alignment_run_id：特定 attempt、输入、配置和时间上下文的对齐版本。
- score_version：参考 MIDI 内容摘要。

数据库的 `visit_attempt_sequence` 是访问内采集序号。训练文件的 `visit_index` 和 `attempt_index` 则在导出时排序计算，不是网页打开时向数据库申请的全局连续号。

### 4.2 visit 生命周期

`AuthProvider` 把认证变化交给 `activity/index.ts`；路由切换不负责建立/销毁 visit。同一已登录用户刷新 token 不会重建访问。

刷新可从 sessionStorage 恢复 visit；新标签页或重新打开建立新的访问；隐藏标签页继续保留访问；站外文档返回的 BFCache pageshow 建立新访问。退出登录结束当前用户访问并中断活跃 attempt。

每 30 秒发送心跳。pagehide 是尽力发送的暂定结束信号，`end_is_inferred=true`。无结束信号时，`student_visit_status` 视图在最后在线超过 5 分钟后给出估计结束；它没有定时向源表写入精确的“关网页时间”。

### 4.3 两套浏览器持久化

`activity/tracker.ts` 的 v2 localStorage outbox 保存小型活动包，sessionStorage 保存当前访问上下文。服务端允许乱序到达，使用 sequence 防止旧心跳覆盖新关闭状态。非法/冲突包进入本地 rejected 区域。

`challenge-history/upload-queue.ts` 的 IndexedDB 保存完整录音包。一个记录绑定 owner 和 attempt ID。成功 finalize 后才删除；网络失败保留，重新登录同一账号、恢复网络、每 30 秒尝试恢复。IndexedDB 失败时保留内存副本并设置离页保护，不能保证抵抗进程强杀。

这两套队列保存不同大小、用途的数据，不是“有 activity 就一定有 MIDI”。当前只有 Challenge 接入完整 attempt 上传链。

## 5. 在线数据库与逻辑关联

### 5.1 访问事实

`student_visits` 存访问开始/结束、last_seen、last_sequence、结束原因及接收时间。

`student_attempts` 存演奏实际开始/结束、所属 visit、visit_attempt_sequence、歌曲。它可早于 `piano_attempts` 存在；没有成功封存上传的演奏也可能在这里有记录。

`student_attempt_events` 存 event_sequence、occurred_at、song_time_sec、elapsed_playing_sec、metadata。事件包括 play_started、paused、resumed、finished、exited、interrupted。

### 5.2 上传与页面读模型

`piano_attempts` 存 learner_key、歌曲、不可变上传 metadata、MIDI/telemetry 的 key 与 hash、状态、finalization、lease_token/lease_until、result_key、alignment_run_id 和 summary。

`challenge_recordings.id` 在新链路中与 attempt ID 相同，提供页面录音列表。`midi_storage_path='gcs:<attempt-id>'` 是逻辑指针，下载时再请求 API 签名。

`play_events_raw` 已停止新写入，保留只读历史。finalize 只向 student_attempt_events 补齐生命周期事件；user_play_logs 是可重建投影，session_id 等于 v2 attempt_id，challenge_recording_id 关联录音。

### 5.3 辅助表

- `piano_learner_keys`：Auth user → learner_key 映射。
- `piano_outbox`：event_type、aggregate_id、payload、published_at。
- `piano_song_catalog`：推荐候选；只读 enabled 项。
- `piano_preferences`：用户偏好；按用户 RLS 管理。
- `piano_recommendations`、`piano_recommendation_feedback`：一次推荐的响应及曝光/点击。
- `profiles`、访问白名单相关表/RPC：沿用 Supabase 身份资料体系。

visit、attempt、event 有显式的用户归属与关联约束。`student_attempts` 和 `piano_attempts` 是使用同一 attempt ID 的逻辑关联，不能把示意关系当成所有字段都建有数据库外键。

## 6. 上传接口与事务

正常新录音按顺序调用：

```text
POST /activity                   练习开始和活动节点；可在上传前持续发送
POST /attempts                   计算摘要后的上传申请
PUT signed performance URL       performance.mid
PUT signed reference URL         reference.mid
PUT signed telemetry URL         performance-events.json
POST /attempts/{id}/finalize      核验完整包并最终提交
```

`POST /attempts` 必须带 schema_version=2，只保存文件身份、learner key、CREATED 任务及服务端 source_environment，不接收 activity/session_id。PUT 签名有效 10 分钟，要求 x-goog-if-generation-match:0。

`POST /attempts/{id}/finalize` 提交 `{schema_version:2, activity}`，其中完整 Activity 包含 owner、Visit、Attempt 和连续事件序列。文件摘要/大小/遥测校验通过后，同一数据库事务完成：

1. 锁定 attempt，与实时 Activity 使用同一 advisory lock。
2. 调用 save_activity 补齐上下文与事件，严格核验已有身份；拒绝遗漏额外事件或覆盖 interrupted。
3. 设置 student_attempts.sealed_at/sealed_event_count。
4. 设置 piano_attempts.UPLOADED + 完整 finalization。
5. 生成 challenge_recordings / user_play_logs 两张读模型。
6. 写入一次 performance.uploaded outbox。

封存后相同事件可重放；修改和新追加拒绝。原始事件浮点值在数据库内部精确比较。`rebuild-projections --attempt-id UUID` 可重建 v2 产品投影。旧 Cycle 字段不再受支持。

## 7. 后台消息与任务状态

### 7.1 Outbox / relay / Pub/Sub

relay 从未发布的 outbox 中 `FOR UPDATE SKIP LOCKED` 领取一条，等待 Pub/Sub 发布完成，再标记 published_at。数据库提交失败可能使同一消息再次投递，消费者必须幂等。

CLI `pianokt-data relay` 默认最多发 1000 条，可以 `--limit` 调整；Python 函数 `relay()` 自身默认值仍为 100，直接调用时要注意区别。

Pub/Sub 一路把已投递消息归档到 raw/events，另一路通过属性过滤，只把 performance.uploaded 推给私有 worker。`/activity` 的实时活动包写 PostgreSQL，本身没有逐包转发到 Pub/Sub 的 trigger。

`worker_api.py:/pubsub` 为每条上传完成通知启动隔离子进程执行 worker，超时 480 秒；失败返回 503，供 Pub/Sub 重试/死信。Cloud Run IAM 是其调用身份边界。

### 7.2 状态含义

```text
CREATED → UPLOADED → PROCESSING → READY
                          └────→ FAILED
FAILED 或租约过期的 PROCESSING 可被重新领取
```

CREATED 仅表示上传申请已登记；UPLOADED 表示文件和业务周期通过最终提交；PROCESSING 表示 worker 已领取；READY 表示结果已持久化；FAILED 表示本次处理异常，可排查后重试。

READY 还需同时检查 summary/diagnostics 的 alignment_quality_valid。质量拒绝是可审计的终态结果，不一定是 FAILED。

worker 领取时写入新 lease_token 和 10 分钟 lease。结果对象先落盘；发布时锁行验证 token，再把 READY、摘要、正式分数和 alignment.completed outbox 一起提交。旧租约不能覆盖新任务，失败回写也受 token 约束。

## 8. 对齐和标签的当前实现

### 8.1 算法分工

- `alignment/time_guided.py`：读原始 MIDI、重建按键证据、检查 transport、聚合和弦、做时间引导的单调匹配。
- `alignment/service.py:align`：校验输入和窗口、组织匹配、质量判断、结果版本与输出。
- `alignment/hands.py`：根据参考 MIDI 的轨道/名称等信息推断左右手并校验。
- `alignment/labels.py`：note-level 错误码。
- `alignment/events.py`：当前 v1.1 onset 数据和六维 KC。
- `prototype.py`、`tracks.py`：保留历史/兼容逻辑；当前 service 不通过 Parangonar 完成匹配。

有按键证据时，使用 playerSongTime 减 reference_origin_sec 进入快照坐标，并核对证据与演奏 MIDI。缺少证据的旧输入采用 MIDI fallback 和受质量门槛约束的校正，不能当作同等完整的新采集。

按观察范围截断参考，把临近起音组成和弦，在有限时间窗口内保持顺序匹配。非单调 seek/loop、坏证据、空观察范围等会影响质量判断；不会先把乱序演奏排序成看起来正常的线性演奏。

### 8.2 三层版本

- 完整 alignment result：`schema_version=3`。
- 当前 matcher：`time-guided-chord-monotonic-v3`。
- `events_data` / `kc_labeler_version`：v1.1。
- attempt 导出 manifest 的外层 schema：1.2。

它们表示不同契约层次，数值不要求相同。run ID 绑定 attempt、两份 MIDI 摘要、配置、matcher 版本和证据/范围等时间上下文。

### 8.3 结果内容

result.json 包含：身份与版本、config、alignment_context、diagnostics、summary、raw_matches、notes、events_data。worker 再附加 learner_key、歌曲、practice_metadata、attempt_created_at、alignment_completed_at 和 telemetry 证据。

notes 是逐音符 match/deletion/insertion 明细；目标音的错误码为 0/1/2/3/4，多弹音没有目标 note 错误码。

events_data 以目标 onset 为索引，包含 onset_seconds、pitches、intervals_seconds、kc_vectors、reject_reason.event/note、timing_offset_ms、pitch_offset_semitones、extra_notes。每条 pitches 是 `[左手音高数组,右手音高数组]`，其他逐音符数组与它同形。

六维 KC 顺序：has_black_key、simultaneous_note_count、both_hands、onset_interval_bin_cs、change_count、mean_move_bin。详细定义见 [schema 说明](../pianokt_schema_README_v1_1.md)。

默认 timing 阈值 150 ms，多弹音挂接窗口 0.25 秒；它们来自 AlignmentConfig，通过显式参数传入，属于工程约定，不能声称是外部已验证标准。

## 9. 文件存储布局

生产新录音在 RAW_ROOT 下的相对路径：

```text
midi/<learner_key>/<attempt_id>/
├─ performance.mid
├─ reference.mid
└─ performance-events.json
matching/<alignment_run_id>/matches.json
alignment/<alignment_run_id>/result.json
events/<PubSub 归档文件>.jsonl
datasets/<一次导出的前缀>/...
```

本地 `align` CLI 的 MIDI 输入副本使用 `midi/<attempt-id>/`，没有真实 learner 目录；它是合成/调试入口，不能套用生产上下文。

Objects 对本地和 GCS 都采用 create-only：相同字节重试成功，异内容冲突。本地通过临时文件 + 原子链接发布；GCS 使用 generation 条件。当前没有自动删除原始研究文件或跨存储永久删除用户数据的完整 API。

## 10. 湖仓有两条输入路径

### 10.1 对齐结果路径

`pipeline/jobs.py:catch_up` 扫 raw/alignment 中未完成的 result，`publish_alignment` 写各层。Delta 表主要由 delta-rs/PyArrow 物化，不是全部由 Spark 计算。

- Bronze alignment_runs：完整来源 envelope。
- Bronze score_notes/performance_notes：原始匹配输入的音符来源。
- Bronze midi_events/transport_segments：按键与暂停/继续分段证据。
- Silver midi_alignment_events：规范化、保留来源的逐音符明细。
- Gold fact_midi_alignment：可信逐音符事实，不复制 payload_json。
- Gold attempt_events：可信逐 onset 标签与 KC。
- Gold fact_alignment_attempt：每个 alignment run 的统计。
- Gold fact_practice_attempt：兼容的较窄统计表，同样按 run 保留版本。
- quality/alignment_runs：质量拒绝或物化验证问题。
- ops/completed_runs、ops/failures：完成集合与处理失败记录。

质量不通过时保留 Bronze、可审计 Silver 与质量记录，跳过 Gold，并写终态完成标记；结构不合法的物化会报错并留下失败记录。

### 10.2 Pub/Sub 事件归档路径

`spark_events.py` 读 raw/events 的 JSONL，先保存原文 Bronze，再解析有效 event_id/event_type，坏行进入 quality/practice_events，有效行进入 silver/practice_events，最后写 ops/practice_event_commits。

Spark 使用 AvailableNow：处理当前已发现的文件后退出；下次用同 checkpoint 继续，不是长期驻留的每键实时评分服务。同 event ID 的异 payload 冲突会报错。

### 10.3 正式读取

Delta 的事务是单表级。alignment 用 completed_runs，practice 事件用 practice_event_commits 作为最后的逻辑发布。

Python 正式读取使用 `Tables.rows(name)` 或多表 `Tables.snapshot(names)`。后者固定提交集合再读各表。`raw_rows` 和直接 Delta 目录读取能看到暂存内容，不能直接作为“业务已完成”的依据。

已完成 run 在同湖仓内保持不可变；`--replay` 不会改写它。修改物化语义时采用新 LAKE_ROOT，重建验收后切换读者。

## 11. 数据集导出

默认 `--source direct --environment production`；Lake 必须显式 `--source lake`。环境在序号分配前过滤 Visit/Attempt/Event/Result。旧 unknown 来源不自动纳入；Manifest 1.3 固化来源与导出时上下文，不能把固定 cutoff 解释成任意未来时刻重查库都得到同一内容。Direct 仅支持当前 READY 提交指针。

两个 CLI 都通过 repeatable-read 读取数据库上下文；`selection.py` 是共同选择入口。lake 依赖 completed_runs；direct 依赖快照中的当前 READY 指针与摘要，不读湖仓。UTC 排序、质量和 waiting 筛选复用。

`export-attempts`：一 attempt 一行，附单独访问/时间线元数据。visit_index 按学生、UTC start、visit ID；attempt_index 按学生、UTC start、visit ID、访问内序号、attempt ID。先编号再筛选。days_since_signup 根据实际开始和注册时间计算。

`export-training`：一个 JSON 内的逐 onset examples，包含 learner、attempt、run、event_index、标签/KC 和 ordering_timestamp/ordering_source。CLI 使用真实 context；无 context 的历史 Python 调用仍可回退上传时间。

指定 `--run-selection` 时只选择映射中的 run；不指定而同 attempt 有多个合格版本时拒绝导出。候选目录不会自动做 latest-wins。

attempt 数据集发布顺序是 intent → 五份数据文件 → manifest。`publication.read_bundle` 检查所有文件摘要。同一 prefix 绑定一份计划，新数据写新 prefix。完整细则见 [导出说明](VISITS_AND_ATTEMPT_DATASET_ZH.md)。

## 12. 推荐与现有页面

`POST /recommendations` 从 PostgreSQL 读取最近至多三次质量有效的 READY 历史，按实际开始时间排序，读取相应 raw 结果，再结合偏好与 enabled 曲目目录调用 DemoRecommender。

当前规则用最近 accuracy 推一个难度目标，并给偏好风格加分。响应带 model_version、is_demo=true、input_alignment_run_ids；预测正确率和学习收益字段为 null。结果与反馈写在线表，反馈同时写 outbox。

当前录音页调用列表、MIDI 下载、演示推荐；未实现完整的对齐状态展示与结果下载界面。GET status/download 接口的存在不能等同于 UI 已有按钮。

## 13. HTTP 与 CLI 入口索引

公开 API 进程提供 GET /health；业务端点使用 Supabase Bearer token 和白名单检查：

- POST /activity：活动包。
- POST /attempts：上传申请。
- POST /attempts/{id}/finalize：最终提交。
- GET /attempts/{id}：状态、summary、error_code、alignment_run_id。
- GET /attempts/{id}/download?kind=performance 或 result：下载签名。
- POST /recommendations、/recommendations/feedback：推荐与反馈。
- POST /debug/local-align：仅明确本地开发开关下启用，无业务鉴权依赖，不上云开放。

私有 worker 进程提供 /health 和 POST /pubsub，其安全依赖 Cloud Run IAM。

`pianokt-data` 命令包括 align、worker、relay、pipeline、spark-events、export-attempts、export-training、rebuild-projections。精确选项以 `cli.py` 和命令 `--help` 为准。

## 14. 云资源与运行边界

Terraform 配置两个 Cloud Run Service：API 和 worker；两个 Cloud Run Job：relay 和 pipeline；一个主 Pub/Sub topic、上传完成 push 订阅、全事件 GCS 归档订阅及死信资源；三个私有桶 raw/lake/checkpoint；Secret Manager 读权限和六类运行身份。

普通镜像运行 API/worker/relay；Spark 镜像供 pipeline 使用，包含 Java 17、Spark 4.0.1、Delta 4.0.0 与 GCS JAR。Python 镜像固定 3.13.12。

调度时区 Asia/Tokyo，默认 relay 10:00/23:00，pipeline 00:00/11:00；schedules_paused 默认 true，lake_pipeline_paused 默认 true，频率可配置。生产服务显式设置 PIANOKT_ENV=production；正式导出和榜单过滤 production。上传到 READY 的等待受 relay 调度和积压影响；不能把异步通道描述成毫秒级服务端反馈。

前端部署产物是 build/client，Terraform 没有负责托管这个静态站点。代码和 Terraform 存在不证明云账号已应用；实际状态需独立验收。

## 15. 兼容边界与未实现部分

旧 Supabase Storage 录音可继续下载；旧播放事件 helper 已删除，016 撤销旧 log/aggregate/save recording RPC 的客户端权限，play_events_raw 不再生产 practice.event。升级按 [部署手册](DEPLOYMENT_AND_ROADMAP_ZH.md) 协调进行。

仓库没有完整自动训练、模型注册、全局数据删除、服务端权威 assignment 校验和生产监控面板。根目录 midi_kc_labeler.py 是独立 raw feature 实验，不是当前线上标签入口；历史 prototype/tracks 文件也不代表当前 matcher。

事务与故障注入测试、未封存录音的恢复限制、云端尚未验收部分见 [重构记录](REFACTOR_AND_DATA_INTEGRITY_ZH.md) 与 [验证记录](VALIDATION_ZH.md)。
