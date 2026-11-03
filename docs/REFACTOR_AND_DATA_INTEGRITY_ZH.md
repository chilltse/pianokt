# 工程重构与数据完整性记录

> 本文是 2026-11-02 至 2026-11-03 的实施记录。阅读当前完整架构，请从 [架构总览](ARCHITECTURE_ZH.md) 和 [实现参考](PIANOKT_SYSTEM_REFERENCE_ZH.md) 开始；这里保留改动原因、测试日期与迁移边界。

实施日期：2026-11-02 至 2026-11-03。范围：浏览器录音与活动记录、API 最终提交、对齐 worker、事件投递、湖仓物化、训练导出及对应测试。本文记录本轮实际修改；之前已有的 visit/attempt 实现见 `VISITS_AND_ATTEMPT_DATASET_ZH.md`。

## 1. “要么都成功，要么都失败”的准确边界

同一个 PostgreSQL 业务操作内，关联表的新增、修改、事件写入和 outbox 写入放在同一事务里。任何一步抛错，连接上下文回滚，不能捕获异常后继续提交剩余步骤。

浏览器、PostgreSQL、GCS、Pub/Sub、多个 Delta 表之间没有共同事务管理器，不能承诺物理上同时提交或同时消失。本实现采用**逻辑原子提交**：先保存不可变输入，再写业务提交标记；正式读者只读已提交数据。失败可能留下未提交文件或待处理记录，但不能把它们显示为完成、拿来评分或用于训练。相同输入可以安全重试，冲突输入必须报错。

因此，“返回网络错误”不等于“服务端一定没有提交”：响应丢失时，使用同一 attempt/event ID 重试，确认已有提交，不创建第二份业务数据。

### 各层的提交单位

- 浏览器：一个 IndexedDB 记录包含 owner、attempt ID、MIDI、reference、telemetry、settings 和完整终止事件序列。事务完成后才发上传请求。
- `/activity`：visit 更新、attempt 上下文、活动事件、结束状态属于一次 PostgreSQL 事务。
- `/attempts`：learner key、活动上下文和 CREATED attempt 属于一次事务。签名 URL 生成在提交之后；生成失败可用同一请求重试。
- `/finalize`：UPLOADED 状态、规范化 finalization、challenge_recordings、play_events_raw、user_play_logs、相关 outbox 一起提交。
- worker：READY、result_key、alignment_run_id、summary、正式分数和 alignment.completed outbox 一起提交；结果对象先创建，未提交前不可通过 READY 读取。
- relay：发布成功后才标记 published_at；发布成功但标记回滚时可能重发。语义是至少一次，消费者必须按稳定 ID 去重。
- alignment 湖仓：各表物理事务独立，`ops/completed_runs` 是最后的逻辑提交。新 run 不覆盖旧 run。
- Spark practice 事件：Bronze、quality、Silver 完成后，最后写 `ops/practice_event_commits`；正式 Silver 读者以这个集合过滤。
- attempt 数据集：`intent.json` 固定整个发布计划；每个文件 create-only；`manifest.json` 最后提交。读取时逐文件校验 SHA-256。

## 2. 浏览器录音恢复与前端边界

新增 `src/features/challenge-history/upload-queue.ts`：队列与存储接口分离，IndexedDB 适配器使用单记录事务封存完整录音包。同一个 owner + attempt ID 的内容不允许变化。

`gcs.ts` 现在负责传输和队列接入：

1. 优先采用 activity 原始 owner，防止完成演奏后切换账号把录音错误归给新账号。
2. 封存数据后，执行 create → MIDI/reference/telemetry PUT → finalize。
3. 每次 API 请求重新确认当前账号等于原始 owner。
4. PUT 使用 create-only generation 条件；412 不直接当成成功内容，最终由服务端重新检查摘要。
5. 只有 finalize 成功返回才删除本地队列项。响应丢失、断网或上传中断均保留数据。
6. 登录、网络恢复、每 30 秒重放当前账号的队列；其他账号的数据保留但不发送。
7. IndexedDB 不可用或配额不足时保留内存副本并重试，未落盘期间设置浏览器离页保护。它无法抵抗浏览器进程被强杀、设备断电或用户强制离开。

`AuthProvider` 在 effect 中启动恢复，避免在 Supabase auth 回调持锁期间递归调用 getSession。

`challenge/page.tsx`：把参数校验放到外层组件，使用 Navigate；后续 hooks 在稳定的 ChallengeAttempt 组件中执行。不同歌曲重新挂载实例。移除 render 中 navigate 和 hooks 条件跳过。主动退出先等待保存结果；失败留在页面展示错误。

`useChallengeAttempt.ts` 统一管理 attempt 的计时、事件序列、活动上报和终止状态，页面保留播放器/录音编排。将钢琴卷帘调试图拆到 `LocalAlignmentTimeline.tsx`；调试手部数据从当前 v1.1 列式数组读取，不再访问不存在的 `events_data.data.left_hand`。修正本地实验界面“没有保存”提示：该接口实际写入本地目录。

`legacy-events.ts` 单独保留历史事件 API。原 `api.ts` 的冗长双 RPC 实现收敛成调用一次原子 RPC，原公开导出兼容保留。重试调用者应固定 eventId 和 clientTs。

修复 `/training/phrases` 重复路由：速度训练使用 `/training/speed`。

## 3. API、冲突与事务隔离

新增 `backend/pianokt_backend/upload_contracts.py`，集中 Upload、Cycle、Finalize、telemetry/window 校验。新增 `upload_service.py`，集中数据库最终提交。HTTP 路由仍负责身份验证、对象检查和事务上下文，业务服务不自行提交事务。

新增校验：

- Cycle 时间必须带时区，不再把无时区时间默认为 UTC。
- 暂停必须来自播放中，继续必须来自暂停中；歌曲位置不允许倒退。
- 相同 finalization ID 重试必须对应相同规范化请求；修改终止原因、事件或元数据返回冲突。
- 已存在 play event 不能只 `ON CONFLICT DO NOTHING` 后声称成功，必须验证 owner、session、类型、时间、歌曲位置、metadata。
- PostgreSQL numeric 比较先转换为传入契约的浮点表示，避免 Decimal 与 float 二进制表示差异误报。
- 已存在 session 不得被另一账号或另一 recording 覆盖。
- finalize 必须包含已记录的完整活动序列，不能遗漏事件。
- finalize 和活动追加使用相同 attempt advisory transaction lock，避免“检查完事件数量后又插入事件”的并发窗口。
- finalized attempt 不再允许追加新的活动事件；原事件的相同重试和 visit 心跳仍可处理。
- recommendation feedback 相同 event ID 若内容不同，返回冲突而不是无声忽略。

`/debug/local-align` 使用 Objects 的原子 create-only 写入，不再直接覆盖 JSON 文件。

## 4. migration 014 和统计口径

新增 `supabase/migrations/014_atomic_attempt_commit.sql`，整个脚本包在 BEGIN/COMMIT 中：

- `piano_attempts.finalization jsonb`：保存最终提交身份，支持重放验证。
- `piano_attempts.lease_token uuid`：worker 租约校验令牌。
- `challenge_recordings.client_accuracy_pct`：客户端反馈分数。
- `challenge_recordings.score_source`：legacy / pending / aligned / rejected。
- 历史聚合 RPC 用 client_ts（缺失才回退 created_at）计算开始/结束时间；完整完成只认 finished，移除 98% 推断。
- backend 已管理的 cycle 禁止被历史汇总 RPC 重写。
- 新增 `log_play_event_atomic`，把历史事件追加、trigger outbox、终止汇总放入单次事务，并检查重复 event ID 的内容。
- 限制客户端对 challenge_recordings、play_events_raw、user_play_logs 的直接写权限；撤销旧裸 log_play_event 的客户端执行权限。新客户端调用原子 wrapper。
- 排行榜正确率只聚合 `score_source='aligned'`。挑战次数仍统计已最终提交的录音；难度仍采用现有业务字段，不宣称已得到正式校准。

新上传最初为 pending。worker 在质量通过且有 accuracy 时写 aligned + accuracy×100；质量拒绝或无有效分数写 rejected。客户端即时得分仅保留在 client_accuracy_pct，不能成为正式排行榜分数。

没有正式分数的排行榜用户显示“—”，不再把 NULL 显示为 0%。个人歌曲最佳成绩也只采用 aligned 行。历史无 score_source 的录音迁移后为 legacy，不自动冒充正式对齐成绩；本轮没有批量修改历史分数。

推荐输入只使用质量有效的 READY attempt，并按实际 started_at 排序；无新上下文的历史记录才回退上传时间。

## 5. Worker 的并发安全

领取任务时生成 lease_token 并随 PROCESSING 一起写入。计算期间先写不可变结果对象；数据库最终发布时锁定 attempt 并验证 token。

旧任务即使在租约过期后计算完成，也不能覆盖后来任务。旧任务失败也只能把仍属于自己租约的任务标记 FAILED，不能清除新任务的 lease 或改坏 READY。

`worker.commit_alignment` 单独暴露为事务内服务，便于直接验证失败回滚。测试在 READY 更新之后故意让分数写入失败，确认 READY 和 outbox 都没有泄漏。

## 6. 标签模块

- `alignment/hands.py`：reference MIDI 解析、轨道手部分配与校验。
- `alignment/labels.py`：唯一的 note reject code 规则。
- `alignment/events.py`：当前 v1.1 onset events 与 KC 特征。
- `alignment/service.py`：匹配编排，使用显式参数绑定 timing threshold 和 insertion window。
- `alignment/prototype.py`：历史双手 exporter 和兼容入口，生产 service 不再动态加载它，也不修改模块全局阈值。

本次只拆分职责，不改变匹配器版本、音符标签含义或 KC 桶边界。配置隔离测试确认两个不同阈值调用互不污染。

根目录独立 `midi_kc_labeler.py` 是已有的 raw feature 实验工具，输出契约与训练的离散 KC 不完全相同，本轮没有把它强行替换为生产 exporter。历史 Parangonar 依赖暂保留在锁定环境中，未把未验证的依赖升级/裁剪混入数据语义重构。

## 7. 湖仓提交和读取

`gold/fact_alignment_attempt`、`gold/fact_practice_attempt` 改为按 alignment_run_id 写入，保留同一 attempt 的各版本。旧的“每 attempt 一行”假设不再成立；消费方必须明确选择 run，不能无条件按 attempt 聚合所有版本。

`Tables.snapshot(names)` 先固定 completed run 集合，再读取多个表并过滤未提交行。`Tables.rows(name)` 使用相同过滤。`raw_rows` 是诊断接口，会看到暂存数据，不用于训练或正式展示。Bronze/quality 本身是审计表，可看到处理失败的输入。

已完成 run 不允许通过 replay 原地重写各层。相同源内容是幂等空操作；不同源内容使用同一 run ID 会报错。修改物化规则需要新 lake namespace/版本，从保留的 raw 数据重建并单独验收后切换读者。

直接使用 Spark/Delta SQL 读取物理目录会绕过上述 Python reader。必须显式 join completed 标记；多表查询必须固定同一标记快照。不能仅凭某张 Gold 表出现了行就判断业务已提交。

Spark 事件流增加：同一 ID 不同 payload 检查、同批冲突检查、最后的 practice_event_commits。历史 checkpoint 已消费的旧数据不会自动获得新提交标记；迁移时使用新 lake/checkpoint 路径重放，避免边写边改旧数据。

catch_up 根据 `alignment/<run>/result.json` 的 run 目录先排除已完成项，再下载 JSON，减少重复读取。没有贸然引入新的分区、数据库或分布式服务。

## 8. 数据集发布与排序

`pipeline/selection.py` 统一 UTC 解析、completed/cutoff 门槛、质量与 waiting 过滤、单版本选择和 attempt 排序键。

两种 CLI exporter 都读取 repeatable-read 上下文。attempt 排序为 learner → UTC started_at → visit ID → visit 内 sequence → attempt ID。排序在质量筛选之前完成，允许有 index 空缺。

新增 `--run-selection selection.json`，内容为 `{ "attempt-id": "alignment-run-id" }`。指定时只导出指定集合；不存在、未提交或超过 cutoff 的指定 run 报错；不指定时仍拒绝同一 attempt 多个候选，绝不自动猜 latest。

attempt 主数据仍只有 visit_index、attempt_index，时间、内部 ID 放独立元数据文件。旧 flat exporter 的排序时间字段改名为 ordering_timestamp，并记录 ordering_source，避免将实际开始时间错误命名为上传创建时间。兼容的无 context Python 调用仍可回退历史上传时间；CLI 使用真实 context，不能混淆两者。

`publication.py`：

1. 完整构造并校验数据文件。
2. 计算每个文件 SHA-256，将 metadata 和哈希作为 create-only intent。
3. 写各个文件；同内容可重试，异内容冲突。
4. 最后写 manifest。
5. `read_bundle` 必须读取 manifest 并检查所有文件摘要。

中断时可能存在 intent 和部分文件，但没有 manifest，因此不是有效数据集。同一个 prefix 已绑定发布计划，不能用于另一份导出；内容变化请用新 prefix。不可变快照记录导出时元数据，不宣称重建了过去任意 cutoff 的全部 visit 活跃状态。

示例（在后端运行环境执行）：

```sh
python -m pianokt_backend.cli export-attempts \
  --cutoff 2026-11-02T00:00:00Z \
  --output-prefix datasets/attempts-20261102-v1 \
  --run-selection selection.json
```

不需要手动选版本且每个 attempt 只有一个候选时，省略最后一行参数。

## 9. 调度、CI、工程清理

- Terraform 新增 relay_schedule / pipeline_schedule，保留已有默认时间和 schedules_paused，不自动改变线上成本或解除暂停。
- relay CLI 新增 `--limit`，默认单次最多 1000 条；可以按负载设置，达到上限不代表 backlog 已清空。
- 部署文档同步默认日本时间 relay 10:00/23:00、pipeline 00:00/11:00。互动场景可配置每 5 分钟 relay；实际完成延迟仍要观察 backlog。
- CI 增加 Python 3.13.12、锁定依赖、PGlite 数据库测试、独立 Java/Spark 集成测试以及前端活动/上传队列测试。
- Bun DOM/parser 测试和 Node 环境注入测试分开执行，避免把 activity 的 import.meta.env 当作普通 Bun 环境执行。
- `.gitignore` 不再隐藏 visual case 的源代码和测试；继续忽略生成产物。必须把这些现在可见的源文件一起提交，否则干净 checkout 的测试集不同。
- 删除两份已跟踪 `.lockb-*.tmp`，新增忽略规则。Bun 和 npm 主锁文件暂保留，未在此轮切换依赖管理器。
- 旧系统/教学手册增加当前契约指引，保留历史说明的来源，不把旧算法说明混同于当前运行逻辑。

## 10. 测试记录

本轮在临时 Python 3.13.12 环境安装 requirements.lock，未替换工作区既有 Python 3.14 `.venv`。隔离数据库为 PGlite，不连接生产 Supabase。

已经覆盖：

- 活动写入中途冲突后，visit 序号和事件数量全部回滚。
- finalize 在已经写状态、录音、事件之后失败，所有相关表及 outbox 全部回滚。
- 同一个最终提交重试不重复生成上传事件；不同内容冲突。
- worker 旧租约不能提交；正式分数写入失败会回滚 READY/outbox。
- 历史聚合按 client time；99% 退出不算 finished；排行榜排除 pending/legacy。
- dataset 中途写失败不可见，相同计划可恢复，异计划冲突，摘要损坏拒绝读取。
- lake 完成标记前注入失败，正式 Silver/Gold reader 不可见，重试恢复；已发布 run 修改被拒绝。
- UTC 顺序、质量过滤前索引、多版本显式选择、上下文缺失与跨用户隔离。
- 浏览器队列网络失败保留录音、按 owner 重放、本地存储失败禁止网络写入。

验证命令与最终结果见本文末尾的“最终验收结果”。

## 11. 部署、恢复与删除约束

这次只修改本地工程，未执行真实数据库迁移、Terraform apply、云端部署或历史数据清理。

建议按一次协调发布执行：

1. 备份数据库，确认回滚部署方案；暂停/排空旧 worker，避免新旧 worker 并发绕过 lease_token。
2. 按序部署尚未应用的 013、014；014 本身整体事务化。
3. 部署新 API / worker，再部署新前端；旧裸事件 RPC 写权限已撤销，使用它的旧客户端需要刷新。
4. 新 lake/checkpoint namespace 重放 Spark 和需要重新物化的历史 run，验证提交标记与读取规则后切换下游。
5. 使用一个新 attempt 验证离线重试、只有一条最终录音、READY 正式分数与 outbox。
6. 再恢复定时任务。旧数据集继续保持不可变，新 exporter 写新 prefix。

回滚应用时不要直接删 014 的列，也不要让旧 worker 与新 worker 同时处理。先停止写入，再选择一套匹配的应用版本和权限配置。不要用回滚 migration 删除已有录音或历史审计数据。

当前系统没有跨 GCS/数据库/湖仓的用户数据永久删除业务 API。本轮没有添加自动删除器或清理未提交文件。直接在云控制台删对象、管理员删除 auth 用户触发 DB cascade、手工改 Delta 表均在这里的业务事务协议之外，不能承诺跨系统原子性。

未来增加修改/删除时必须沿用以下规则：数据库内一事务；跨系统先原子提交逻辑删除/替代版本及 outbox，正式读者同时过滤该状态，再异步幂等执行物理清理。不能“先删文件再删数据库”，也不能把破坏旧不可变快照当作普通更新。物理永久清理需要独立的引用检查、保留策略和恢复验收。

## 12. 尚未宣称完成的事项

- 浏览器强制关闭前未封存的正在录音片段，目前仍只有活动中断信息，不保证有完整 MIDI。要恢复每一个未结束 take，需要另做分片持续录音和恢复协议。
- 真实浏览器 IndexedDB 配额/BFCache、真实 GCS/Pub/Sub、生产角色授权仍需 staging 端到端验收；单元测试不能替代它。
- ChallengeAttempt 已拆出路由边界、生命周期 hook、调试视图、持久上传和历史 API，完整播放器时间控制仍保留现有实现；未重写整个 Player/React 生命周期为新状态机。
- 尚未生成全套 OpenAPI→TypeScript 客户端；这次抽离了后端契约和可测试服务，未虚称跨语言 DTO 已自动生成。
- 数据量分区、全链路指标 dashboard、不同方案基准测试、原型 KC 工具统一、移除历史依赖/锁文件，需要后续独立验收。本次只做有确定性收益的已完成对象跳过和模块拆分。
- 全项目格式检查存在既有 27 个文件不符合 Prettier 的基线问题；本轮修改的核心文件已格式化，没有把无关页面全部重新排版。

这些边界不应被“所有测试通过”掩盖。跨系统一致性依赖所有正式读者遵守提交协议；绕过协议读暂存表，不在保证范围内。


## 13. 持久规则与改动范围

新增 `.cursor/rules/data-integrity.mdc`，alwaysApply=true，将本次用户要求变成后续编码的常驻规则：事务内更新、跨存储提交标记、幂等身份、worker fencing、删除协议、失败测试和文档同步。

本轮新增模块：

- `backend/pianokt_backend/upload_contracts.py`、`upload_service.py`、`publication.py`。
- `backend/pianokt_backend/alignment/hands.py`、`labels.py`、`events.py`。
- `backend/pianokt_backend/pipeline/selection.py`。
- `src/features/challenge-history/upload-queue.ts`、`legacy-events.ts`。
- `src/pages/challenge/LocalAlignmentTimeline.tsx`、`useChallengeAttempt.ts`。
- `supabase/migrations/014_atomic_attempt_commit.sql`。
- `backend/tests/test_publication.py`、`src/features/challenge-history/upload-queue.test.ts`、`scripts/test_upload_queue.mjs`。

现有模块的修改已分别在前文记录；对应测试扩展在 test_activity、test_api、test_attempt_dataset、test_core、test_spark、test_time_guided_alignment 及 activity_db.mjs。

工作区开始时已有 .gitignore、README、infra、package、auth、challenge-history、challenge/recordings 页面、backend_role 的未提交修改，以及 visit tracker、013、示例数据、独立 KC 脚本和本地文档等新增文件。本轮在它们之上继续修改，没有回滚、提交或把这些既有工作冒称为本轮新增。视觉测试源文件原先被 ignore 隐藏，本轮只是恢复其可提交性。

## 14. 最终验收结果（2026-11-03）

- Python 3.13.12 + requirements.lock + 隔离 PGlite：**44 passed, 1 skipped**。跳过的是需显式开启的 Spark 测试。
- 独立开启 Java 17 / Spark 4.0.1 / Delta 4.0.0 集成测试：**1 passed**，包含 checkpoint 恢复、坏数据隔离、提交集合及重复 ID 冲突后的可见性验证。因此 45 个后端测试均已分别执行通过。
- `npm run test:activity`：**7 passed**。
- `npm run test:uploads`：**2 passed**。
- `npm run check-types`：通过。
- `npm run build`：通过；仍有已有的大 bundle 和 React Router future flag 提示，不影响构建成功。
- 本轮修改的核心 TypeScript/TSX 文件 Prettier 检查通过。
- `terraform fmt -check infra/gcp/main.tf`、`git diff --check`：通过。
- 全项目 `npm run check-fmt` 仍有前述 27 个既有文件的格式基线问题；整个 infra 目录的 fmt 检查还会报告本地 terraform.tfvars。未改写本地云配置或宣称全仓格式检查已通过。
- Bun 专用 parser/TimelineStrip/referenceSnapshot 测试本地未重跑；CI 中保留了独立执行步骤。当前新增队列与活动测试使用 Node 实际执行。
- 没有真实 Supabase/GCS/Pub/Sub 端到端验收，也没有执行 migration、apply 或部署。迁移上线前需遵循第 11 节的协调发布顺序。

核心命令（本机临时依赖目录仅用于此次验证）：

```sh
PGLITE_MODULE=/tmp/pianokt-test-db/node_modules/@electric-sql/pglite/dist/index.js \
  /tmp/pianokt-python313/bin/python -m pytest backend/tests -q

SPARK_LOCAL_IP=127.0.0.1 \
  PYSPARK_SUBMIT_ARGS='--master local[2] --conf spark.jars.ivy=/tmp/pianokt-spark-ivy pyspark-shell' \
  PIANOKT_TEST_SPARK=1 \
  /tmp/pianokt-python313/bin/python -m pytest backend/tests/test_spark.py -q

npm run test:activity
npm run test:uploads
npm run check-types
npm run build
```


## 15. 当前架构文档统一（2026-11-03）

用户要求用清晰语言说明全工程，并让旧架构说明与当前实现一致。本轮只调整文档：

- 新增 ARCHITECTURE_ZH：从浏览器、数据库、文件、后台、湖仓、导出六部分解释全链路，并提供一张数据流图。
- 重写 SYSTEM_REFERENCE：替换旧的 4570 行混合历史手册，以当前模块/模型/接口/状态/提交协议为查询入口。
- 重写 TEACHING_GUIDE：跟随一次开始、暂停、继续、提前退出的 Challenge 解释实际代码。
- 同步 AUTH_AND_RECORDINGS、LOCAL_DEVELOPMENT、DEPLOYMENT、VISITS_AND_ATTEMPT_DATASET，纠正新录音存储、完整队列、正式评分、014 升级、调度和版本导出说明。
- 更新 README 阅读入口、CHANGES 演进摘要、VALIDATION 记录来源与边界，以及根目录 schema 的当前实施状态。
- 去掉将旧 Parangonar、仅 ID 缓存、裸 RPC 双写、旧手部结构、逐 attempt 覆盖和未实现页面查看器描述为当前实现的正文。

按前后端入口、activity、上传服务、worker、alignment、pipeline、migrations 和 Terraform 逐项核对；检查本地 Markdown 链接目标与文档结构。没有在此次纯文档更新中重新运行全部业务测试或执行真实数据库/云资源变更。上一节验收数字保留其实际日期与范围。


## 16. 按需直接导出（2026-11-03）

新增显式 DB＋GCS 来源与 migration 015 提交摘要；保持旧 lake 默认路径、在线协议及调度。文件级变更、迁移顺序、限制、测试和回滚见 [简化实施记录](DATA_PIPELINE_SIMPLIFICATION_IMPLEMENTATION_ZH.md)。
