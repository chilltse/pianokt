# PianoKT 当前工程架构

更新：2026-11-04；代码与 migration 016。云端需按部署手册应用迁移和发布。完整字段见 [数据字典](DATA_CONTRACT_V2_ZH.md)，实施决策见 [v2 精简结果](pianokt_data_architecture_simplification_plan.md)。

## 1. 系统分工

浏览器负责乐谱、播放、MIDI 输入与即时反馈。PostgreSQL 负责身份、低频行为事件、上传处理状态及产品查询。GCS 保存不可变媒体、遥测和对齐结果。Worker 负责异步评分。正式训练文件默认从 PostgreSQL READY 提交指针 + GCS Result 直接导出；Lake/Spark 是显式启用的分析路径。

在线上传仅用于 Challenge；Freeplay/Training 页面存在不意味着它们都要上传云端演奏。

## 2. 主链路

1. 登录后创建/恢复 Visit，Activity tracker 持久化低频心跳和事件；本地 Alignment Lab 不启动该 tracker。
2. 实际播放开始建立 Attempt，记录 play_started。倒数不计入录音；暂停/继续记录相同字段契约的事件。刷新或意外离开记录 interrupted。
3. 正常结束或主动退出后，先将 owner、完整 Activity、MIDI、参考快照、遥测和设置封存到 IndexedDB v2。
4. POST `/attempts` 登记文件摘要，得到 GCS create-only 签名 URL；创建状态为 CREATED。此接口不写演奏事件。
5. 浏览器 PUT performance.mid、reference.mid、performance-events.json。412 只代表对象存在，最终仍由服务端核验摘要。
6. POST `/attempts/{id}/finalize` 提交 `{schema_version:2, activity}`。服务端验证文件、时间轴和完整事件序列，在同一事务调用 Activity 幂等写入、封存、设置 UPLOADED、生成投影和 performance.uploaded outbox。
7. Relay 至少一次投递 Pub/Sub；私有 Worker 通过租约领取任务、核验原始证据、创建不可变 Result，然后原子提交 READY、result_digest/result_committed_at、正式分数和 alignment.completed outbox。
8. Direct Export 在一个短 repeatable-read 事务内取得同环境上下文和 READY 指针；离开事务后读取 GCS、验证摘要和身份、应用质量门，最后发布不可变数据集。

## 3. 事实、状态与投影

- `student_visits`：访问事实与暂定在线状态。
- `student_attempts`：每一次开始演奏，即使没有录音；结束状态是事件派生值，sealed 字段标记最终封存。
- `student_attempt_events`：唯一生命周期事件权威表，sequence 在 Attempt 内唯一。
- `piano_attempts`：上传/处理状态、文件 Hash/Key、完整 finalization 身份、当前结果提交指针。
- `piano_learner_keys`：账号到研究假名的映射。
- `challenge_recordings`：录音列表和分数投影；客户端分数单独保留，正式分数来自 Worker。
- `user_play_logs`：最终周期汇总投影；主键 session_id 等于 v2 attempt_id。
- `piano_outbox`：事务消息；不再为新 Challenge 重复发逐条 practice.event。
- `play_events_raw`：退役的只读历史，旧客户端写 RPC 已关闭。

`rebuild-projections --attempt-id UUID` 从已封存 v2 finalization 和当前状态/summary 重建两张投影，不重跑算法、不追加 outbox、不更改源事件。

## 4. 状态机与失败恢复

```text
CREATED → UPLOADED → PROCESSING → READY
                         └──────→ FAILED → PROCESSING
PROCESSING 超过租约期限也可重新领取
```

CREATED 的文件可能只上传一部分；UPLOADED 表示原始证据及周期已原子提交；READY 表示处理完成，不等于质量通过。失败保留文件和队列，使用相同身份重试。Worker 以 lease_token 防止失效任务覆盖新提交。

浏览器只在 finalize 成功后删除 v2 Bundle。IndexedDB 失败时保留内存并阻止常规离页；未落盘前的断电无法恢复。浏览器关闭后不运行上传，下一次登录同一账号才恢复。

实时 Activity 与 finalize 使用同一个 attempt 锁和事件写入器。封存后允许相同事件重试，拒绝新增或修改；已有 interrupted 不可被 finished 包覆盖。数据库、GCS、Pub/Sub 没有跨系统事务，读取者必须以数据库 READY/UPLOADED 和数据集 Manifest 作为提交边界。

## 5. 环境与研究语义

服务端 `PIANOKT_ENV` 决定 source_environment，未设置视为 development。旧记录 unknown，不自动晋升 production。正式榜单和默认导出仅纳入 production。导出在环境筛选后再分配 Visit/Attempt 序号，在质量筛选前保留真实演奏次数。

完成方式、客户端成功判定、对齐分数、训练资格是四种不同信息。`user_play_logs.exit_status` 保持客户端语义；正式分数只读 `score_source=aligned`；训练另需质量通过且非 waiting。

数据集 v1.3 保存环境及上下文快照。可复现的是已发布产物，不是任意未来时刻按相同 Cutoff 重查可变数据库。Direct 不提供历史 READY 指针时光回溯。

## 6. 可选 Lake 与文件

GCS raw 保存 MIDI、按键侧车、matching/result JSON、Pub/Sub 事件归档以及导出文件。Result v3 当前仍内嵌遥测；不存在本轮已实现的“只留引用”格式。

Lake 桶是 Delta/Parquet 表，checkpoint 桶保存 Spark 检查点。它们没有参与在线 finalize 或 READY 的事务。Terraform 保留资源，`lake_pipeline_paused=true` 默认单独暂停物化，`--source lake` 显式使用旧分析基线。

## 7. 模块边界

| 模块 | 职责 |
|---|---|
| `src/features/activity` | Visit 生命周期、实时事件持久化与 owner 隔离 |
| `src/pages/challenge/useChallengeAttempt.ts` | 单次演奏的事件和累计时长 |
| `src/features/challenge-history/gcs.ts` / `upload-queue.ts` | v2 Bundle 封存、签名上传、finalize 和重试 |
| `activity.py` / `upload_contracts.py` | 事件幂等写入、完整周期与遥测校验 |
| `upload_service.py` / `projections.py` | 原子封存和可重建产品投影 |
| `provenance.py` | 服务端环境来源、导出允许值 |
| `worker.py` / `worker_api.py` | Relay、租约、对齐与 READY 提交 |
| `pipeline/direct.py` / `attempts.py` / `training.py` | 环境筛选、提交校验、研究导出 |
| `publication.py` | intent/files/manifest 原子发布协议 |

推荐仍是 DemoRecommender，未包含训练完成的 KT 模型。生产配置、数据隔离、数据库迁移和 GCS 权限需实际部署验证，不能由本地测试结果推断。
