# 异步 finalize：持久接收、验证与恢复

更新：2026-11-05。依赖 migration 017；事件驱动派发已在代码实现，本轮没有新增迁移或部署云服务。实施记录、线上配置差异及发布限制见 [事件驱动派发实施记录](RELAY_EVENT_DRIVEN_IMPLEMENTATION_ZH.md)。

## 协议与数据流

1. 浏览器将完整演奏封存到 IndexedDB v2。创建上传仍为 `POST /attempts`、`schema_version=2`，登记对象 Key/Hash/侧车数量/设置，形成数据库中的文件清单。文件上限为每个 MIDI 8 MB、侧车 4 MB。
2. 最多三份 GCS 文件并行 create-only PUT。全部成功或 412 才提交 finalize；412 不代表内容已验证。
3. `POST /attempts/{id}/finalize` 使用 **`{schema_version:3, activity:完整Activity}`**。API 仅做认证、所有权/环境/事件身份和时序校验，在一个事务内补齐事件、封存、写 ACCEPTED/accepted_at/finalization、录音列表投影、performance.submitted outbox。提交成功后，可在独立上下文中即时发布持久 Outbox（`DIRECT_PUBSUB_ENABLED` 默认 false，ACK 预算默认 2 秒）；发布失败仍返回 **202**，正文 `{id,status,validated_at}`。这里没有 GCS 读取或对齐计算；API 认证和数据库耗时仍存在。
4. API 快速路径或低频 Relay 发布 outbox；私有 Pub/Sub Worker 领取带 lease_token 的任务，进入 VALIDATING。检查文件大小、实际 SHA-256、MIDI 解析、完整侧车契约，GCS 读取使用 generation 前置条件。验证通过后记录 validated_at/file_generations、进入 PROCESSING，并生成 user_play_logs。
5. Worker 复用本次已验证的 MIDI 文件和侧车数据进行对齐，不重复下载；提交 Result、READY 和 alignment.completed。重试时使用已记录的 GCS generation，避免引用不同代对象。
6. 浏览器恢复首先查询已有 attempt：ACCEPTED/VALIDATING/PROCESSING/RETRY 不重复签名或上传；只轮询状态。例外：Worker 明确报告未验证文件缺失（NotFound/FileNotFoundError）时，恢复包重新获取签名并尝试补传，仍禁止覆盖已有对象。**有 validated_at 才清理本地 Bundle**，无需等待评分 READY。INVALID/未验证 FAILED 保留恢复包并在录音列表显示错误。

旧 finalize v2 请求返回 422，提示刷新前端，防止旧队列误把异步 202 当成可删除本地文件的确认。历史数据库 finalization v2 仍可读取/重建投影；本地 Bundle v2 不变，新前端可使用其内容发 finalize v3。异步接收后不允许修改文件身份或重新写入不同 finalization。

## 状态和事实边界

`CREATED → ACCEPTED → VALIDATING → PROCESSING → READY`

- ACCEPTED：完整提交已持久入库；不声称文件内容正确。
- VALIDATING：Worker 已领取，正在下载和验证。
- PROCESSING：文件验证通过、validated_at 有值，正在评分。
- RETRY：暂时错误，等待 next_retry_at；使用有上限的指数退避，最长 30 分钟。
- INVALID：摘要、MIDI 或侧车契约不合法；消息确认、不自动重传错误文件，保留证据。需修正客户端后新录，不能修改原 Attempt 以掩盖问题。
- FAILED：8 次处理尝试耗尽，包括连续进程崩溃导致的过期租约；需要人工排查。不是自动无限循环。
- UPLOADED：历史同步验证后的状态，新请求不产生；新 Worker 仍支持处理。

Worker 租约 10 分钟，私有 HTTP Worker 子进程超时 480 秒。所有结果提交和失败更新都由 lease_token 防止旧 Worker 覆盖新任务。重复 READY/INVALID/FAILED、有效租约中的 VALIDATING/PROCESSING、未到 next_retry_at 的 RETRY 通知直接确认；不会再次领取或增加 processing_attempts。过期租约与到期重试仍允许原子领取，低频恢复扫描不能关闭。CREATED/不存在不会当作已完成。

录音列表在 ACCEPTED 时就能显示，并每 15 秒刷新状态；未验证时禁用下载按钮。浏览器上传恢复每 30 秒/恢复在线/新任务时检查服务端状态，不是持续高频轮询。

公开榜单要求 production 且对应 piano_attempts.validated_at 非空；正式正确率仍只统计 aligned。新 user_play_logs 延后到文件验证成功才建立。Direct Export 的结果仍只读 READY 已提交指针；Visit/Attempt/生命周期事件仍是观测事实，包括未完成或无有效录音的演奏，不能把它们误称成已验证演奏结果。

## 新数据库字段（piano_attempts）

- accepted_at timestamptz?：持久接收时间。
- validated_at timestamptz?：文件验证通过时间，客户端恢复包清理依据。
- file_generations jsonb?：performance/reference/performance_events 对应的 GCS generation 字符串；本地文件测试路径为空字典。
- processing_attempts integer NOT NULL DEFAULT 0：成功领取 Worker 的次数。
- next_retry_at timestamptz?：暂时故障的最早重试时间；终态不再据此重试。
- validation_error text?：安全的文件校验失败原因，最长 500 字符。
- status CHECK 扩展为上述状态；error_code 继续保存错误分类。

迁移只对已经存在 finalization 的旧非 CREATED 记录回填 accepted_at/validated_at，因为旧 API 同步验证过；回填时间是历史近似值，不是重新验证时间。不会回填旧对象 generation，下一次 Worker 运行才记录实际 generation。

## 派发与补偿

复用数据库 piano_outbox 和 Pub/Sub；新增事件 performance.submitted，payload 仍为 attempt_id、learner_key、source_environment。订阅和 Worker 同时接受历史 performance.uploaded。

提交后的快速路径使用 `outbox_dispatch.py`，与 Relay 共用 `FOR UPDATE SKIP LOCKED`、消息编码、有限 ACK 等待和 published_at 确认。没有把关键任务放在 FastAPI BackgroundTasks 中。ACK 成功但确认提交失败时允许同一 event_id 重发。

Terraform `relay_schedule` 的最终默认值为每 15 分钟，`schedules_paused=false` 才会运行，Lake 保持 `lake_pipeline_paused=true`。首次发布先显式使用每分钟补偿，确认直发和兼容订阅后再降频。202 会增加有界 ACK 等待和额外 DB 开销，2 秒不包括整个请求；Job 启动、调度和网络仍可能增加延迟。Worker 崩溃恢复可能先等剩余 10 分钟 lease，再等下一次扫描。

**本轮只读发现：线上订阅只接收 uploaded，Scheduler 是 `0 10,23 * * *`。不得直接开启 submitted 直发或 apply 可能重建订阅的计划。** 先按实施记录做旧订阅积压保护与并行迁移；生产 017 仍需单独核实。

每次 relay 先扫描：已发布但仍滞留 ACCEPTED/UPLOADED、到期 RETRY、过期 VALIDATING/PROCESSING 租约。满足条件的旧 outbox 重新设为待发布，允许重复通知。达到重试上限的过期任务转 FAILED。补偿不依赖浏览器，Pub/Sub 死信/短暂通知丢失不等于任务永久遗失；前提是 relay 调度确实启用且数据库、IAM、消息服务可用。

关页发生在服务端接收前：需下次同浏览器同账号恢复。发生在接收后：持久任务继续运行，客户端无需保持页面打开。若用户清除 IndexedDB，未确认验证的本地副本仍可能丢失；服务端已上传文件与已接收任务不因此删除。

## 协调升级顺序

1. 安排窗口，暂停旧版本新提交及旧 Worker/relay 消费，确认当前 schema 到 016。
2. 应用新增 `017_async_finalization.sql`，不要重跑旧迁移。
3. 发布新 Worker/relay 和 API，保留原环境/身份配置；新 API 要求 finalize v3。
4. 更新 Pub/Sub 过滤器，接收 submitted 和 uploaded；过滤器修改可能触发订阅重建，先评估 Terraform plan 和旧积压。数据库 outbox/attempt 补偿保留恢复来源，不能手工清空它们。
5. 启用分钟级 relay：检查实际 tfvars 是否覆盖默认值，设置 schedules_paused=false。先确认能处理 ACCEPTED，再发布新前端并刷新旧标签页。
6. 验收正常录音、关页后完成、丢失 202 响应后恢复、重复消息、文件缺失后恢复、永久校验错误、Worker 崩溃恢复、未验证数据不进榜单/演奏汇总。

本轮数据状态和 API 语义都改变，不能仅发布前端或单独回滚旧 Worker。未部署前，线上 finalize 仍会按旧实现耗时。

## 验证与限制

新增隔离数据库测试覆盖 ACCEPTED→验证→READY、首次 finalize 无 GCS 访问、永久失败不进榜单/汇总、缺失文件退避与 outbox 重新派发、耗尽租约转 FAILED；存储测试覆盖 generation 固定和大小限制先于下载。前端测试覆盖收到接收确认不清理、验证后清理、恢复只读状态不重传。

未做真实 GCP 端到端部署或耗时对比；同步等待减少来自移除文件读取，并不是承诺所有环境都能在固定毫秒数内返回。Bundle 打包、提前签名、GCS 事件替代 finalize 均未实施。

017 初始版本历史检查：后端 79 passed / 4 skipped（3 项真实 PostgreSQL 并发、1 项 Spark）；前端类型检查、生产构建、协议/恢复测试与 Terraform validate 通过。真实生产队列/IAM/网络耗时须部署后验收。

最新事件驱动版本的测试结果和未完成的云端验收见 [实施记录](RELAY_EVENT_DRIVEN_IMPLEMENTATION_ZH.md)。
