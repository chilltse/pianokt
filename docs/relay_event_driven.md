# PianoKT 事件驱动派发、低频补偿与冷启动优化：Codex 实施计划

**状态：代码已按本规格实现并进行隔离测试，尚未云端部署。线上订阅和调度存在差异，发布前必须处理；详见 [实施与验收记录](RELAY_EVENT_DRIVEN_IMPLEMENTATION_ZH.md)。**  
**基准代码：** `chilltse/pianokt`，`feature/3_layer_kc`，2026-11-05 已核对的版本。  
**生产环境：** GCP `pianokt`，`us-central1`，Supabase PostgreSQL，GCS，Pub/Sub，Cloud Run，Terraform。  
**前置依赖：** Challenge v2 / 异步 Finalize v3；线上数据库须已完成 `supabase/migrations/017_async_finalization.sql` 的协调升级，未完成时先停在预发布阶段。  
**目标：** 低流量情况下尽量使用免费额度；正常新演奏无需等待定时 Relay 就能触发 Worker；失败时可靠补偿；减少不必要的 Python 初始化和镜像成本。

> **给 Codex 的首要指令**：先读取本分支的真实代码、测试、Terraform 及数据库迁移，核对本文件的前提是否仍成立。按“最小可测试改动”实现；不要擅自执行生产 Terraform apply、Supabase SQL、部署、迁移或删除资源；不要修改现有 v3 上传协议、身份认证、严格幂等与租约语义。遇到数据库唯一约束、线上订阅过滤器或 Terraform state 与设计不一致时，停止相关危险操作，先给出差异与安全替代计划。

---

## 0. 一页结论：最终架构与边界

```text
用户浏览器
  └─ POST /attempts/{id}/finalize  (schema_version=3)
       └─ FastAPI：同一 DB 事务
             1. 完整 Activity 验证、封存
             2. piano_attempts = ACCEPTED
             3. piano_outbox 写入 performance.submitted
             4. COMMIT（持久接收边界）
       └─ COMMIT 后（新的上下文、有限超时）
             ├─ 直接 Pub/Sub publish 成功 -> 标记 outbox.published_at
             └─ 失败/超时/实例退出       -> 保留 Outbox 待补偿
       └─ 返回 HTTP 202（不等待 MIDI 验证或评分）

                         ┌──────────── Pub/Sub push ────────────┐
                         ▼                                     │
                私有 Cloud Run Worker                           │
              VALIDATING -> PROCESSING -> READY                │
                   \-> INVALID / RETRY / FAILED                 │
                                                               │
每 15 分钟 Cloud Scheduler -> Cloud Run Relay Job                │
    └─ 补发未发布 Outbox、到期 RETRY、过期 lease、漏掉的通知 ───┘

Spark Pipeline 保持暂停；Direct Export、GCS 对象契约和前端协议不变。
```

**重要选择：本次不把 Relay Job 改成 Service。** 对每 15 分钟才运行一次的补偿任务，先采用 1 vCPU / 1 GiB 的 Job（测量通过后可降到 512 MiB）；不引入额外 HTTP 入口或常驻实例。Service 在每分钟触发且每次很短时通常更便宜，但这里已经用事件驱动代替了高频空跑。

**本次必须完成（P0）**：提交后立即派发、Outbox 保底、Worker 重复消息处理、Relay 低频化与轻量导入、API 轻量导入、Terraform IAM/环境变量、测试、观测与文档。  
**本次先测不盲改（P1）**：Worker `subprocess.run` 每次新进程的开销、Docker 镜像拆分、进一步缩小内存或 CPU、改变评分执行方式。

---

## 1. 当前源码事实（改动前核对）

| 文件 | 当前实现 | 需要做什么 |
|---|---|---|
| `backend/pianokt_backend/api.py` | `finalize()` 在 `with connect()` 内执行 `commit_finalization()`；退出后直接 HTTP 202。API 顶层 `from .alignment.service import align`，但在线生产的此模块主要用于开发 `/debug/local-align`。 | 在数据库连接上下文成功退出 **之后**执行即时派发；将仅本地 Lab 所需的 Alignment import 延迟到函数内。 |
| `backend/pianokt_backend/upload_service.py` | `commit_finalization()` 原子写 ACCEPTED、投影和 `performance.submitted` 到 `piano_outbox`；相同 finalization 有幂等返回路径。 | 获得持久化 Outbox 的 `event_id`；保持原有事务和身份检查；不在函数内调用 Pub/Sub。 |
| `backend/pianokt_backend/worker.py` | 顶层导入 `.alignment.service`（进而导入 `numpy`、`pandas`）；同时包含 `process()`、`recover_pending()`、`relay()`。 | 将 Relay 和补偿逻辑拆到轻量模块；Worker 仅负责文件验证、评分和状态提交。 |
| `backend/pianokt_backend/cli.py` | `relay` 分支执行 `from .worker import relay`，导致 Relay 也加载 Alignment。 | 变成从轻量的 Outbox 模块 import relay。 |
| `backend/pianokt_backend/worker_api.py` | 接受 `performance.submitted` / `performance.uploaded`，每个评分请求使用 `subprocess.run([python, -m, pianokt_backend.cli, worker, attempt])`，超时 480s。 | 保留子进程及硬超时（P0 不移除）；测量进程额外开销；消费重复消息应返回成功而非不必要的 503。 |
| `backend/requirements.lock` | 一个镜像同时安装 numpy/pandas/scipy/pyarrow/deltalake/partitura 等较大依赖。 | 第一阶段只优化导入；不急于重写整套依赖与 Dockerfile。 |
| `infra/gcp/main.tf` | API/Worker Service 各 2 vCPU/2 GiB；Relay/Pipeline Job 共用资源块 2 vCPU/4 GiB；默认 Relay `* * * * *`；API 尚无 Event Topic publisher IAM；Relay 已有 publisher IAM。 | 给 API 细粒度 Publisher IAM 与 EVENT_TOPIC；Relay 独立资源；调度改 15 分钟并保留 Pipeline 暂停。 |
| Pub/Sub Terraform | Worker 订阅源码已接受 `performance.uploaded OR performance.submitted`。 | 先核对线上配置；**不得盲目重建带积压消息的订阅**。 |

**关键区别：** 镜像大可能影响镜像获取、冷实例创建；Python 顶层 import 影响每个冷进程初始化；Worker 的子进程每次都 import 即使 Cloud Run HTTP 实例保持温热。这三类耗时需要分别测量。

源文件：
- https://github.com/chilltse/pianokt/blob/feature/3_layer_kc/backend/pianokt_backend/api.py
- https://github.com/chilltse/pianokt/blob/feature/3_layer_kc/backend/pianokt_backend/worker.py
- https://github.com/chilltse/pianokt/blob/feature/3_layer_kc/backend/pianokt_backend/cli.py
- https://github.com/chilltse/pianokt/blob/feature/3_layer_kc/backend/pianokt_backend/worker_api.py
- https://github.com/chilltse/pianokt/blob/feature/3_layer_kc/infra/gcp/main.tf
- https://github.com/chilltse/pianokt/blob/feature/3_layer_kc/docs/ASYNC_FINALIZATION_ZH.md

---

## 2. P0-A：持久 Outbox 与即时派发

### 2.1 严格事务边界

`piano_attempts.status='ACCEPTED'` 和 `performance.submitted` Outbox 在**同一个数据库事务**中写入，COMMIT 成功才允许调用网络 Publisher。

发布成功与 HTTP 202 是两个独立事实：

- **数据库 COMMIT 失败**：不能发布 Pub/Sub；Finalize 按原语义返回错误；不能写出一个并不存在的任务。
- **COMMIT 成功，Pub/Sub publish 失败 / ACK 超时**：Finalize **仍返回 HTTP 202**，Outbox 留存，等待 Relay 补偿。
- **Pub/Sub ACK 成功，标记 `published_at` 失败**：允许 Relay 重发同一个 `event_id`；不能谎称数据库确认已写入。
- **COMMIT 成功，API 在 publish 前崩溃**：Outbox 必须可被低频 Relay 找到。
- **用户重复 finalize 同一内容**：仍走既有幂等判断，不产生新的业务事件；不同 finalization 仍返回 409。
- **API 不等待 Worker**：202 表示持久接收，既不是文件已经校验成功，也不是结果 READY。

不允许在 Finalize 主事务中执行任何 Pub/Sub 或 GCS 网络操作；也不允许用未受保障的线程/`BackgroundTasks` 代替 Outbox。请求后的尽力而为任务可以是加速路径，但绝不能作为可靠性唯一来源。

### 2.2 查明 Outbox 现有数据库约束

**Codex 必须先查数据库 migration 与测试**，确认 `piano_outbox` 的主键、`event_type`/`aggregate_id` 唯一性以及 `ON CONFLICT DO NOTHING` 的确切冲突条件。不要假设它天然保证“一次 Attempt 一条 submitted”。

实现：
1. `commit_finalization()` 保留一次提交中的现有 insert，必要时使用 `RETURNING event_id`。
2. 重试时如果已有相同 finalization，查询**已有** `performance.submitted` Outbox 及其状态，不生成新 ID。
3. `event_id`/payload 必须来自**已提交数据库**中的 Outbox 记录，不能从未持久提交的 Python 临时对象或前端自报字段单独重建。
4. 若唯一约束不满足，先在隔离数据库检查历史重复，设计**新增 migration 018（仅在确需时）**，不得修改已经应用的旧迁移文件；避免约束与其他 `piano_outbox` 事件类型发生冲突。

### 2.3 统一发布模块

建议新增：`backend/pianokt_backend/outbox_dispatch.py`，其中实现：

- `publish_outbox_event(event_id, *, timeout_s=...)`：按 `event_id` 从 DB 取已提交事件；与 Relay 共用编码、ACK 与 `published_at` 语义。
- `relay(limit=...)`：批量派发未发布事件，数量有界。
- `recover_pending(c)`：从原 `worker.py` 挪入，保留原有状态机 SQL。
- 惰性导入 `google.cloud.pubsub_v1`，进程内复用 `PublisherClient`（注意生命周期和测试隔离），不在 import 模块时建立网络连接。

保持**当前线上 Pub/Sub 消息契约**：

```python
payload = dict(outbox_row['payload'],
               event_id=str(outbox_row['event_id']),
               event_type=outbox_row['event_type'])
# publish(..., json.dumps(payload).encode(), event_type=outbox_row['event_type'])
```

其他 `alignment.completed`、`recommendation.*` Outbox 仍由 Relay 派发；本次快速路径仅处理 `performance.submitted`，不静默丢弃其他类型。

**并发与锁**：最小实现可以在**与 Finalize 完全分离的短事务**中，对单条 Outbox `SELECT ... FOR UPDATE SKIP LOCKED`，Pub/Sub 获得 ACK 后设置 `published_at` 再提交；所有发布者（API 与 Relay）共用此代码，避免两个发布者在无锁状态下重复提交发布。该事务在等待远程 Pub/Sub ACK 时会持有**该条 Outbox 行锁**，所以必须严格设置有限的发布等待时间，不能持有任何 Finalize/Attempt 业务锁。若因此造成明显长事务或竞争，再设计 claim/lease 发布字段及单独 migration，不要为了优化把幂等保护删掉。

任何时刻都必须容忍至少一次重复投递：Pub/Sub ACK 已返回但数据库提交 `published_at` 失败、future 超时但实际送达、API/Relay 进程崩溃都可能造成重复。重复是允许的，重复评分、状态回退和重写不可变结果不允许。

### 2.4 修改 API

文件：`backend/pianokt_backend/api.py`、`upload_service.py`。

参考逻辑（**伪代码，不要求照抄签名**）：

```python
@app.post('/attempts/{attempt_id}/finalize', status_code=202)
def finalize(...):
    with connect() as c:
        row = owned_attempt(c, attempt_id, uid)
        result = commit_finalization(c, row, uid, body)
        # 仅返回已存在的 Outbox ID/receipt；这里不能 publish

    # with connect() 正常退出并确认 COMMIT 后才能执行
    if DIRECT_PUBSUB_ENABLED and result.event_id is not None:
        try:
            publish_outbox_event(result.event_id, timeout_s=2.0)
        except EXPECTED_PUBLISH_OR_DB_ERRORS:
            log_safe_failure_without_secrets()
            # 不改变已经成功的接受事实

    return result.receipt
```

要求：
- 新增 `DIRECT_PUBSUB_ENABLED`，开发/测试默认 false，生产灰度后 true，支持快速回退。
- 超时初始预算建议 2 秒作为实验配置，不承诺稳定 2 秒；记录 `pubsub.publish`, `outbox.confirm`, `http.finalize` 分阶段耗时。
- `pubsub` 出错只记可运维的安全日志（event_id、request_id、错误类型和阶段），**不要记录 token、DATABASE_URL、签名 URL、完整消息 payload**。
- 202 响应体、Frontend IndexedDB `validated_at` 清理依据和 `/attempts/{id}` 查询契约保持不变。
- 若复用既有 Finalize 且 Outbox `published_at IS NULL`，可尝试补发；已标记 published 时不必再发。
- 同步的有界 Pub/Sub ACK 会增加 202 延迟；此行为是有意的、需要监控。如果慢到不可接受，单独评估新的可靠异步触发方案，不把无保证线程称为可靠架构。

---

## 3. P0-B：Worker 重复消息与可靠恢复

### 3.1 修复有效租约时无意义的 HTTP 503

现有 `worker.py:process()` 原子更新领取 Attempt；若领取失败且不是 `READY`/`INVALID`/`FAILED`，会抛 `Attempt busy or not finalized`，`worker_api.py` 随后返回 503，使 Pub/Sub 不断重试。

领取失败后再查状态，按下表处理；**领取本身仍然用原子 `UPDATE ... RETURNING`**：

| 数据库状态 | 收到重复通知时的行为 |
|---|---|
| `READY` / `INVALID` / `FAILED` | 成功 ACK，不再评分。 |
| `VALIDATING` / `PROCESSING` 且 lease 有效 | 另一 Worker 已领取；成功 ACK，原 lease 保持，等待其完成或过期。 |
| `RETRY` 且 `next_retry_at > now()` | 成功 ACK 当前过早消息，由低频补偿到期重投。 |
| `ACCEPTED` / 旧 `UPLOADED` | 原子尝试领取；若并发败给他人，则重新判断当前 lease。 |
| `VALIDATING` / `PROCESSING` 但 lease 已过期 | 允许原子竞争重新领取；不能仅 ACK 而永久遗忘。 |
| `RETRY` 已到期 | 允许按原子领取条件重试。 |
| `CREATED` / 不存在 | 不能当作已完成；记录安全的异常诊断，按现有错误/消息重试策略处理，不无限风暴。 |

**保留**原 10 分钟 lease、`lease_token` fencing、最多 8 次处理、不可变 GCS 对象和 READY 发布事务；确保竞争失败本身不增加 `processing_attempts`。因活跃 lease 提前 ACK 的消息，未来依赖过期租约补偿；不能删掉定时恢复。

### 3.2 Relay 补偿行为不变，调度降低频率

将 `worker.py` 中的 `recover_pending(c)` 与 `relay(limit)` 移入 `outbox_dispatch.py`，通过 import 保持 CLI 正常工作；相关测试迁移到新模块路径。保留这些行为：

- `published_at IS NULL` 的 Outbox 派发，含非上传类低优先级事件。
- `ACCEPTED` / `UPLOADED` 已发布但长期未领取的消息重新激活。
- `RETRY` 且 `next_retry_at <= now()` 的任务重新投递。
- `VALIDATING` / `PROCESSING` 的过期租约恢复。
- 超过上限的任务标记 `FAILED`，不能无限重试。
- `FOR UPDATE SKIP LOCKED`/幂等发布；一次 Relay 的处理量有上限，避免无界运行或单次调度持续到下一次。

**恢复延迟不是恒定 15 分钟**：尚未发布的 Outbox 通常要等到下次扫描（最多约 15 分钟 + 启动/服务延迟），但已在处理中的 Worker 如果崩溃，还必须先等原本 **10 分钟租约过期**，再等下一次扫描（约 0–15 分钟）与 Worker 执行。若业务要求更快失败恢复，需调低补偿间隔或设计独立的到期重试触发，不能声称所有失败都 15 分钟内恢复。

---

## 4. P0-C：冷启动与导入优化（不重构业务）

### 4.1 Relay：最先优化的项目

现在的入口链：

```text
pianokt-data relay
  -> cli.py: from .worker import relay
  -> worker.py 顶层 from .alignment.service import align
  -> alignment/service.py 顶层 numpy / pandas ...
```

Relay 只需要 `psycopg` + Pub/Sub + 少量 SQL/日志，不应该加载对齐引擎。执行：

1. 在独立 `outbox_dispatch.py` 实现 `relay()` / `recover_pending()` / Publisher。
2. 将 `cli.py` 的 `relay` 分支直接 import `outbox_dispatch.relay`。
3. 确认 `python -X importtime -c 'import pianokt_backend.outbox_dispatch'` 不会加载 `pianokt_backend.alignment.service`、`numpy`、`pandas`、`scipy` 等。
4. 保留原 `pianokt-data relay` 命令名、`--limit` CLI 参数、数据库行为与返回值，不能破坏现有 Cloud Run Job Terraform。

### 4.2 API：只在开发 Lab 使用时加载 Alignment

现在 `api.py` 顶层含 `from .alignment.service import align`。将其移到 `/debug/local-align` 函数内部：

```python
@app.post('/debug/local-align')
def local_align(...):
    if not local_alignment_lab_enabled():
        raise HTTPException(404, 'Not found')
    from .alignment.service import align
    ...
```

目标：生产 API 冷启动不因未调用的实验型 Alignment 端点而加载 NumPy/Pandas。不要删除 Lab 功能，也不要改变其开发环境限制。检查其他顶层 transitive import；**仅移动实测无业务影响的大模块**，不要盲目重排所有 import。

### 4.3 Worker：先测再决定，第一版保留 subprocess

当前 `worker_api.py` 的 HTTP 应用比较轻，但每条消息执行：

```python
subprocess.run([sys.executable, '-m', 'pianokt_backend.cli', 'worker', attempt],
               check=True, timeout=480)
```

即使 Cloud Run HTTP 服务保持热实例，每次子进程也要启动 Python 并 import Alignment。这是**每次消息的应用初始化**，不是 Cloud Run 服务每次都冷启动。第一版：

- 保留 `subprocess.run` 的 480s 硬超时和进程隔离，**不要直接替换成线程或在 Web 进程内同步调用**，避免破坏故障隔离。
- 添加不泄露身份/录音内容的 `worker.subprocess.total` 计时；Python import 时间在测试环境单独测量。
- 如果子进程初始化明显占主要耗时，先优化 `cli.py` 和 `worker.py` 的无关导入；若需改进程模型，提交独立设计评审（超时取消、并发、内存泄露、OOM、lease、Cloud Run timeout 均需重新验证）。

### 4.4 同一个镜像先不拆分

继续使用当前 `backend/Dockerfile` 打包单个普通 Backend 镜像，供 API、Worker、Relay 共用；`backend/Dockerfile.spark` 不改。镜像安装重型依赖会增加镜像大小，但**安装了不代表每次启动都导入**。先测镜像大小、冷启动、导入耗时，之后才决定是否创建轻量 Relay image / requirements 分组；避免在本次可靠性改造中同时引入三套构建和发布流程。

---

## 5. P0-D：Terraform 与资源配置

### 5.1 API 的 Topic 权限和开关

在 `infra/gcp/main.tf` 为 API 服务账号增加对 **指定 Topic** 的 `roles/pubsub.publisher`，复用现有身份，不创建或下发私钥：

```hcl
resource "google_pubsub_topic_iam_member" "api_publisher" {
  topic  = google_pubsub_topic.events.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.runtime["api"].email}"
}
```

在 API（不是前端、不是 Worker）环境变量中配置：

```text
EVENT_TOPIC = google_pubsub_topic.events.id
DIRECT_PUBSUB_ENABLED = false   # 首次部署先关闭
```

Terraform 建议新增变量 `direct_pubsub_enabled`，默认 false，变更通过 plan/apply 管理。使用 Python ADC/Cloud Run Service Account；浏览器端不接触 GCP Publisher 凭据。确保 service deployment 在 IAM 授权就绪后再启用，避免发布刚开始全部 403。

### 5.2 Relay Job 与 Scheduler

在现有 `google_cloud_run_v2_job.job` 按 `each.value` 区分资源，不改 Pipeline：

```hcl
resources {
  limits = each.value == "relay" ? {
    cpu    = "1"
    memory = "1Gi"   # 优先稳定；验证内存峰值后可以改 512Mi
  } : {
    cpu    = "2"
    memory = "4Gi"
  }
}
```

在 `terraform.tfvars` 或已有环境配置中：

```hcl
relay_schedule        = "*/15 * * * *"
schedules_paused      = false
lake_pipeline_paused  = true
```

- Relay：每 15 分钟一次，作为 Cloud Run Job，min instances 不适用。
- API / Worker：保留 Cloud Run Services，按请求使用；保持最小实例为 0，不额外支付全天保温费用。
- Spark Pipeline：仍暂停。Direct Export 不受影响。
- Relay `--limit` 默认现有 CLI 为 1000，保留且测量耗时、检查是否有 backlog 长期积压；必要时缩小单次批量或用有界批处理。
- Job 原有 `max_retries=2`、timeout 1800s；后续观察空跑异常，避免频繁失败重试产生额外费用。

### 5.3 Pub/Sub 与 Terraform state 安全

现有源码 Worker 订阅过滤器含 `performance.uploaded` 和 `performance.submitted`；部署前必须执行只读检查，不能因为 GitHub 已配置就断言线上一致。

```bash
gcloud pubsub subscriptions describe pianokt-alignment-worker \
  --project=pianokt --format='yaml(name,filter,topic,pushConfig)'

gcloud scheduler jobs describe pianokt-relay \
  --project=pianokt --location=us-central1 --format='yaml(schedule,state)'
```

Terraform 初始化必须复用现有 remote state，不能意外创建第二套 state。检查 `terraform plan` 是否有 `google_pubsub_subscription.worker` 的 `-/+` 替换：Pub/Sub 过滤器不可直接修改，若需替换先制定双订阅/积压保护/并行切换步骤，绝不盲目 apply。保留现有私有 Worker IAM/OIDC，不能为省事开放匿名 Worker。

---

## 6. P0-E：测量和验收门槛

### 6.1 本地导入基准（改前、改后各运行）

```bash
# 假设在仓库根目录、Python 环境已安装 backend
/usr/bin/time -p env PYTHONPATH=backend python -c 'import pianokt_backend.api'
/usr/bin/time -p env PYTHONPATH=backend python -c 'import pianokt_backend.worker'
/usr/bin/time -p env PYTHONPATH=backend python -c 'import pianokt_backend.outbox_dispatch'

env PYTHONPATH=backend python -X importtime \
  -c 'import pianokt_backend.api' 2> /tmp/pianokt-api-import.log

env PYTHONPATH=backend python -X importtime \
  -c 'import pianokt_backend.outbox_dispatch' 2> /tmp/pianokt-relay-import.log
```

**改造前尚无 `outbox_dispatch`，先使用原 `worker` import 记录 Relay 间接依赖；改后再运行新模块测量**。至少比较三次新进程，报告 wall time 和是否加载 numpy/pandas，不将单次结果当作生产 SLA。

### 6.2 Cloud Run 指标分开测量

- Cloud Run 实例冷启动：容器启动到接收请求的耗时（使用平台可见启动延迟监控/日志）。
- API 模块导入与应用 lifespan 初始化（当前启用轻量连接池，min size=0）。
- API `http.finalize`、DB `db.transaction`、`pubsub.publish`、`outbox.confirm` 阶段；p50/p95 和失败比例。
- Worker 服务 HTTP 首次请求、每次 Python 子进程运行总时间、`worker.validate` 和 Alignment 业务耗时。
- Relay Job 执行时长、峰值内存、空队列执行时间、每次派发条数、每月执行次数。
- Outbox：未发布数量、最老待发布等待时间、`RETRY` 到期未处理数量、过期 lease 数量、重复消费次数。

**区分网络 RTT 与冷启动**：SQL 与 Supabase/Cloud Run 区域距离导致的延迟，不能统计成 Python import 时间；Warm API 的 SQL 慢也未必是容器冷启动。

### 6.3 自动测试——必须覆盖

- [ ] Finalize 事务失败/回滚：绝不调用 Publisher，数据无 ACCEPTED/Outbox 提交。
- [ ] 首次 COMMIT 成功：只生成一个 `performance.submitted` Outbox，后置 publish 正常。
- [ ] ACK 成功：消息字节、`event_id`、`event_type` 属性与既有 Relay 一致；之后标记 `published_at`。
- [ ] ACK 超时/发布异常：仍返回 202，Outbox 保留未发布；Relay 可补发。
- [ ] 发布 ACK 成功但确认 SQL 失败：允许重发；Worker 结果只能有一个有效 READY。
- [ ] COMMIT 后 publish 前进程崩溃：Outbox 保留，下次低频 Relay 能补发。
- [ ] 重复相同 Finalize 不新增业务 Outbox；不同内容 409，原记录不变。
- [ ] API 和 Relay 同时竞争发布同一条 Outbox：无死锁；不会丢消息；允许有界的至少一次投递。
- [ ] Pub/Sub 同一 Attempt 重复通知：有效 Worker lease 下第二条 ACK，而不导致 503 风暴或再次领取。
- [ ] `RETRY` 未到期先 ACK、到期可补偿；过期 lease 可恢复，达到 8 次转 FAILED。
- [ ] 旧 `performance.uploaded` 消息仍能处理；`alignment.completed` 等非上传消息仍由 Relay 派发。
- [ ] Relay 新 import 路径不触发重型 Alignment import，`pianokt-data relay --help` 与原 CLI 功能保持一致。
- [ ] API `/debug/local-align` 只在允许的本地模式可用，移走顶层 import 后结果不变。
- [ ] Worker 子进程 480s 超时、异常触发重试以及 lease fencing 的既有测试通过。
- [ ] 不改变 Auth、RLS、来源环境过滤、GCS create-only、MIDI 摘要验证与 `validated_at` 清理逻辑。

隔离 PostgreSQL/PGlite/模拟 Publisher 可用于失败注入；真实 Pub/Sub IAM/OIDC/CORS、真实 Cloud Run Job 内存、Subscription 重试必须在隔离测试项目里验证。**禁止通过杀死生产 Worker、删生产订阅或修改真实用户 Attempt 做故障注入**。

### 6.4 成功标准

1. 正常 Challenge：Finalize 持久提交后立即派发，通常不等待 15 分钟 Scheduler；Worker 验证通过后进入 READY。
2. API Publisher 失败：客户端仍收到 202，业务事实仍在库里，低频补偿能恢复。
3. 相同消息多次到达：不会产生重复评分、不必要的 503 重试风暴或终态回退。
4. Relay 运行内存不 OOM；在目标负载下能在计划的时间内处理积压；API 冷启动与 Worker 子进程耗时至少有改前改后的真实数字。
5. 每月计费风险可解释：空载时无持续 API/Worker 实例费用，Relay 不再每分钟冷启动一个 2 vCPU/4GiB Job。

---

## 7. 发布与回滚：沿用 VERSION + 完整 Git SHA

### 7.1 改造和发布阶段

1. **核对基线**：确认生产数据库已应用 017，记录当前 API/Worker/Relay 镜像、Revision、Scheduler、Pub/Sub filter、Terraform state 与 Outbox backlog。若缺 017，先按现有文档独立完成协调升级。
2. **完成 P0 并本地测试**：先改 Outbox 与 Worker 状态逻辑，再改导入和 Terraform；保留开关 `DIRECT_PUBSUB_ENABLED=false`。
3. **提交源码**：`git status` 确认工作树；commit；用 `SHA=$(git rev-parse HEAD)` 取得完整 40 位 SHA，使用 `backend:<VERSION>-<SHA>` 的**唯一镜像标签**。
4. **构建镜像**：当前 `backend/Dockerfile` 的 `COPY requirements.lock` / `COPY .` 要求 build context 为 `backend/`。
5. **Terraform 先行**：为 API 配置 Topic 权限和 `EVENT_TOPIC`、Direct 开关为 false；检查 `terraform plan`，尤其订阅重建、Scheduler、镜像覆盖与 IAM。
6. **更新 API/Worker/Relay**：同一个普通 Backend 镜像；API 先创建不承载生产流量的新 Revision，确认兼容后切流；Relay 仍保持较高频直到快速路径已验收。
7. **开启直发**：按 Terraform 变量逐步将 `DIRECT_PUBSUB_ENABLED=true`，用受控演奏确认 Pub/Sub ACK、Worker READY、日志和 API 额外延迟。
8. **降低 Relay 频率、资源**：改为 15 分钟，1 vCPU/1GiB；内存和耗时测量通过后再评估 512Mi。Pipeline 保持暂停。
9. **观察与费用控制**：确认账单预算提醒、Job 重试次数、Outbox 积压指标；记录发布的 SHA、镜像 digest、Cloud Run Revision 与最终 Terraform plan。

构建命令示意：

```bash
export PROJECT_ID=pianokt
export REGION=us-central1
export VERSION=0.10.0  # 示例；与已有版本规划核对后选唯一版本
export SHA="$(git rev-parse HEAD)"
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/pianokt/backend:${VERSION}-${SHA}"

gcloud builds submit backend --project="$PROJECT_ID" --region="$REGION" --tag="$IMAGE"
```

Terraform 仅使用既有 remote state，不复制一套新的生产状态。**Codex 只应实现与提供命令，不要自动 apply/deploy，除非用户另行明确授权。**

### 7.2 快速回滚

- 关闭 `DIRECT_PUBSUB_ENABLED`，让数据库 Outbox 继续累积且由 Relay 派发。
- 临时把 Relay 从 15 分钟恢复为 1–5 分钟（注意 Job 费用），或手动执行一次 Relay Job 处理积压。
- 不删除 `piano_outbox`、不删除仍未 READY 的 Attempt、不清空死信队列、不回滚 migration 017。
- 镜像回滚仅允许返回**兼容 async finalize v3** 的版本；不能为了回滚把旧 v2 API 与新 v3 前端或旧 Worker 随意混跑。
- 如 Pub/Sub filter 或 Terraform plan 需要替换现有订阅，使用独立的无损迁移步骤；禁止未经评估地 destroy/recreate。

---

## 8. 费用估计与注意事项

按每 15 分钟一次、30 天、2,880 次、Job 每个任务实例最低按 60 秒、Relay 1 vCPU/1 GiB、每次实际运行不超过 60 秒且无重试：

- 172,800 vCPU 秒/月；172,800 GiB 秒/月。
- 低于常见美国 Tier 1 Cloud Run Jobs 免费额度（240,000 vCPU 秒、450,000 GiB 秒），**但只有结算账号仍有相应免费额度时才可能 $0**。
- 其他费用仍有 Supabase、Worker 执行、Cloud Run API、Pub/Sub、GCS、数据跨区域传输、Logging、Artifact Registry 与 Cloud Build；不能承诺整个产品每月一定 $0。
- 不为 API/Worker 设置最小实例 1（除非未来有量化延迟收益和预算），防止 24 小时实例费用。
- 每次 Job 运行超过 60 秒、任务失败重试、或一个调度周期还未结束又触发下次执行，均会提高真实用量。以 GCP Billing 为最终事实来源。

---

## 9. Codex 交付与汇报要求

交付可审阅的代码差异（尽量分为独立 commit/PR）：

| 交付项 | 文件/目录 | 完成标志 |
|---|---|---|
| 共享 Publisher 与 Relay/补偿拆分 | 新 `outbox_dispatch.py`、`worker.py`、`cli.py` | Relay 不加载 Alignment；事件消息格式不变 |
| API 提交后立即发布 | `api.py`、`upload_service.py` | 事务边界、202 与 Outbox 保底正确 |
| Worker 幂等与重复 ACK | `worker.py`、必要时 `worker_api.py` | 重复消息不会生成 503 风暴 |
| API 导入轻量化 | `api.py` | 仅本地 Lab 调用时 import Alignment |
| Terraform | `infra/gcp/main.tf`、`infra/gcp/terraform.tfvars.example` | Topic IAM、环境变量、Job 资源与调度可 plan |
| 自动测试 | `backend/tests/` | 全部核心正确性与失败注入通过 |
| 监控及文档 | `docs/ASYNC_FINALIZATION_ZH.md`、`docs/DEPLOYMENT_AND_ROADMAP_ZH.md` 等 | 新架构、运行命令、费用和回滚一致 |
| 基准报告 | Markdown 或测试输出摘要 | 提供改前/改后 import、Job 内存、Worker 子进程、API Finalize p50/p95 数据；未测的项目如实标“待测” |

**Codex 最终回复必须包含**：具体修改文件、关键实现决策及潜在竞态、测试命令与实际结果、未完成/跳过测试、是否需要新 migration、Terraform plan 的风险、人工部署手顺、预计成本条件和回滚方式。不可把代码通过单测说成线上已经切换。

### 交给 Codex 的验收口令

> 只实施“提交后即时 Pub/Sub 派发 + 持久 Outbox + 15 分钟低频 Relay Job + 冷启动导入优化”。保留 v3 协议、Worker 子进程硬超时、GCS 校验与 idempotent/lease fencing；不新增常驻 Service、不改 Cloud Tasks、不迁移 Supabase、不动 Spark Pipeline、不擅自执行生产变更。先验证真实数据库约束和现有测试，再修改；所有新成本结论必须附免费额度假设及实际监控方案。
