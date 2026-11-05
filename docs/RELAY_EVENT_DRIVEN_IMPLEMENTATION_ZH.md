# 事件驱动派发：实现、验证与分阶段发布记录

日期：2026-11-05。代码已实现，未部署；未执行生产 apply、迁移、SQL 或发布。
规格：[relay_event_driven.md](relay_event_driven.md)。

## 实现与事实边界

- `backend/pianokt_backend/outbox_dispatch.py`：API/Relay 共用 Publisher、消息编码、ACK 与数据库确认。Pub/Sub 惰性导入；Publisher 线程安全地按进程复用并在 API lifespan/Relay CLI 退出时关闭。空队列不创建云客户端。
- `upload_service.py`：业务事务仍原子写 ACCEPTED、封存、投影和 Outbox。INSERT RETURNING 获取持久事件 ID；幂等重试查询原 Outbox，不新建业务事件。HTTP 收据仍是 `{id,status,validated_at}`，内部事件 ID 不暴露在响应里。
- `api.py`：主连接上下文成功退出后才调用快速派发；开关默认 false，ACK 预算默认 2 秒。任何派发或确认异常不撤销已经提交的接收事实，仍返回 202。Lab 的 Alignment import 移到本地访问检查之后。
- `worker.py`：原子 UPDATE 领取、10 分钟 lease、8 次上限及 fencing 保持。终态、有效 VALIDATING/PROCESSING lease、未来 RETRY 直接 ACK，不增加处理次数；到期 RETRY 和过期 lease 仍通过原条件领取。
- `worker_api.py`：保留子进程及 480 秒硬超时，增加 `worker.subprocess.total`。子进程失败/超时仍返回 503。
- `cli.py`：`relay` 直接导入轻量模块，保留 `--limit` 默认 1000，新增 `--max-seconds` 默认 600；达到数量或时间预算后不再领取下一条。时间预算不是强制杀进程的硬截止：最后一条的 DB/连接初始化仍有额外耗时。Cloud Run Job 的 1800 秒硬超时与 2 次重试不变。
- Terraform：API 获得指定 Topic 的 publisher IAM、EVENT_TOPIC 和开关；Service 最小实例明确为 0。Relay Job 1 CPU/1Gi，Pipeline 仍 2 CPU/4Gi。最终 Relay 默认调度为每 15 分钟，首次发布需显式覆盖为每分钟，待直发验证后再降低频率。

`009_lakehouse_backend.sql` 已定义 `PRIMARY KEY(event_id)` 与
`UNIQUE(event_type,aggregate_id)`，隔离 PostgreSQL 已验证这些真实约束及 017 字段。
本次无需新增 migration 018，也未改已有迁移。生产 017 是否完整应用仍需人工核对，
不能由本地迁移测试推断。

## 发布竞态与时限

快速路径仅发布 `performance.submitted`。Relay 继续发布全部事件类型，包括历史
`performance.uploaded`、`alignment.completed` 和推荐事件。

所有发布者在独立事务中 `FOR UPDATE SKIP LOCKED` 领取 Outbox 行，然后发送消息，
收到 ACK 才更新 published_at 并提交。等待 ACK 只持有这条 Outbox 行锁，不持有
Finalize 的 Visit/Attempt 锁。并发发布者跳过已锁行；没有把数据库事务锁替换成
进程内锁。`outbox.confirm` 计时包含确认 UPDATE 和事务退出；只有提交成功才输出
`outcome=confirmed`。

消息字节保持 `json.dumps(dict(row['payload'], event_id=str(row['event_id']),
event_type=row['event_type'])).encode()`，Pub/Sub 属性保持 `event_type`。数据从提交后
重新读取的数据库行取得，不从浏览器或临时 payload 重建。

SDK 单次 RPC 设置有限 timeout 且关闭隐式重试，future ACK 等待有预算。2 秒不是整个
HTTP 的 SLA：主事务、额外 DB 获取/确认、首次 ADC/Publisher 初始化与网络调度均不在
这 2 秒内。超时 future 仍可能实际送达；ACK 后进程退出或确认提交失败也可能重发。
因此只保证至少一次通知；Worker 的 lease/fencing 和不可变结果负责避免重复有效结果。

已有相同 finalization 但 Outbox 被异常删除时不会捏造新事件，会输出
`stage=outbox.existing outcome=missing`，需要运营排查。

补偿继续处理未发布、长期未领取、到期 RETRY、过期 lease 和重试耗尽。恢复等待可能是
剩余 10 分钟租约 + 下一次 0–15 分钟扫描 + Job/Worker 启动/执行时间，不承诺 15 分钟内
恢复所有崩溃。

## 已发现的线上差异：禁止直接套用当前配置

本轮仅使用 gcloud describe 做只读核对，结果如下：

- `pianokt-alignment-worker` 的线上过滤器只有 `performance.uploaded`，源码已有
  `uploaded OR submitted`。直接发布 submitted 不会让当前旧订阅触发 Worker。
- `pianokt-relay` Scheduler 实际是 `0 10,23 * * *`、ENABLED，并不是分钟级。
- 本地已经初始化的 backend 为 GCS，bucket `pianokt-pianokt-tfstate`、prefix
  `pianokt/dev`。本次没有初始化新 backend，也没有复制生产 state。
- `terraform validate` 通过。实际只读 `terraform plan -input=false -lock=false`
  尝试长时间无输出，已中止，最终为 operation canceled；没有取得有效 plan。
  因此不能声称没有其他 drift、镜像覆盖或资源替换。

过滤器变化会涉及不可原地修改的订阅配置。**不要 apply 当前整体计划或重建旧订阅。**
安全替代步骤应独立审阅：保持旧订阅的原 filter、OIDC、积压与 DLQ；新增一个不同名称、
只接收 submitted 的私有 Worker push 订阅，复制适当的重试/DLQ/IAM 配置并纳入同一
Terraform state；验证新事件后再处理旧 uploaded 消息的排空和退役。
这样旧订阅的积压不会因替换丢失。迁移设计确定前，不自动创建、删除或改名任何订阅，
也不在主代码中假定迁移已完成。

## 人工发布顺序

1. 在目标数据库人工运行 `backend/diagnostics/relay_health.sql` 的只读检查，确认 017、
   Outbox 唯一约束、pending/backlog、RETRY 和 lease 状态；记录现有 Revision、镜像 digest
   及队列积压。若发现约束不一致或缺少 017，停在预发布并单独处理。
2. 先解决上面的 Pub/Sub 订阅迁移和 Terraform plan 问题。复用现有 remote state；
   不运行 README 中用于新项目的 state bucket 创建/init 示例。
3. 提交经审阅源码后，用 VERSION 和完整 Git SHA 构建唯一镜像，build context 必须为
   `backend/`。本轮没有替用户 commit、build 或 deploy。
4. 首轮计划保留 `direct_pubsub_enabled=false`，显式使用
   `relay_schedule="* * * * *"`、`schedules_paused=false`、`lake_pipeline_paused=true`。
   复核 API Topic IAM、私有 Worker OIDC、镜像变更及所有 destroy/replace，再由人工批准。
5. 更新兼容 v3 的 API/Worker/Relay，先用无生产流量的新 API Revision 验证。
   API Service depends_on Topic IAM；浏览器不获得发布凭据。
6. 在受控环境开启 `direct_pubsub_enabled=true`，完成实际演奏，观察主 COMMIT、Pub/Sub ACK、
   outbox.confirm、Worker 验证和 READY。检查 IndexedDB 仍只按 validated_at 清理。
7. 验证失败补偿和 Relay Job 内存后，再通过独立计划将调度降低为 `*/15 * * * *`。
   Relay 保持 Job，1 CPU/1Gi；API/Worker min instances=0，Spark Pipeline 仍暂停。

可在既有配置中生成分阶段计划（只给命令，本轮未 apply）：

```bash
terraform -chdir=infra/gcp plan \
  -var='direct_pubsub_enabled=false' \
  -var='relay_schedule=* * * * *' \
  -var='schedules_paused=false' -var='lake_pipeline_paused=true'
```

以上命令在订阅迁移方案落实前仍可能提出危险替换；它不是可直接执行 apply 的授权。
真实 IAM、OIDC、Pub/Sub 超时/重投、Cloud Run Revision、Job 内存与冷启动只能在隔离
GCP 项目或经过批准的灰度环境验收。不得杀生产 Worker、删除订阅或修改用户 Attempt
做故障注入。

## 本地验证

依赖与隔离 PostgreSQL/PGlite 的准备步骤见 `backend/diagnostics/README.md`。
本轮测试使用独立 Docker PostgreSQL 17，所有数据库随机命名并在测试结束后删除；
Publisher 全部为模拟对象，没有向真实 Topic 发布消息。

```bash
PGLITE_MODULE=/private/tmp/pianokt-test-db/node_modules/@electric-sql/pglite/dist/index.js \
PIANOKT_TEST_POSTGRES_DSN=postgresql://postgres:pianokt_test@127.0.0.1:55432/postgres \
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q

node scripts/test_upload_protocol.mjs
node scripts/test_activity_tracker.mjs
node scripts/test_upload_queue.mjs
terraform -chdir=infra/gcp validate
```

本轮实际结果：后端 **206 passed / 1 skipped**；跳过的是未开启
`PIANOKT_TEST_SPARK=1` 的 Spark 集成测试，真实 PostgreSQL/PGlite 均已运行。
最后的派发计时/日志调整另跑 Outbox、并发和 API 定向回归，39 项通过。
前端上传协议测试通过，Activity 7 项、上传队列 4 项通过；Terraform validate 通过。
Terraform 远程 plan 未完成；真实 GCP IAM/OIDC/发布与上述云端性能指标尚未验收。

关键测试：`test_outbox_dispatch.py` 覆盖事务失败不发布、ACK 超时、ACK 后 SQL/COMMIT
失败、相同事件补发、不同 finalize 的 409、默认开关关闭、消息字节、其他事件类型、
预算、客户端复用与日志隐私；`test_pg_outbox.py` 用独立真实连接验证已提交数据可见、
API/Relay 并发 SKIP LOCKED 和重复 Worker 只有一次领取。
`test_worker_dispatch.py` 验证有效租约/未来重试 ACK、到期/过期领取、最多 8 次、旧消息、
HTTP 503 与 480 秒超时契约；`test_light_imports.py` 验证轻量导入及 Lab 实际对齐仍可用。
已有 MIDI 摘要、GCS generation、lease fencing、结果不可变、RLS 与前端恢复测试继续运行。

## 本地导入基准

每项为 3 次独立 Python 进程的 wall time（秒），包含解释器启动；不是 Cloud Run SLA。
机器负载与文件缓存会影响结果，Worker 未做进程模型优化，不能把样本差异解释为业务加速。

- API 改前：0.9841 / 0.4309 / 0.4332；改后：0.3712 / 0.2199 / 0.2949。
  改前加载 NumPy/Pandas/Alignment，改后均不加载。
- Relay 旧入口间接导入 Worker：0.4290 / 0.4118 / 0.4182；新 Outbox 模块：
  0.1022 / 0.0995 / 0.1009，均不加载 Alignment、NumPy、Pandas、SciPy。
- Worker 模块改前：0.4290 / 0.4118 / 0.4182；改后：0.8687 / 0.5068 / 0.4519。
  仍加载 Alignment；本轮不宣称 Worker 初始化变快。
- Worker HTTP 模块改前：0.1555 / 0.1556 / 0.1509；改后：0.1599 / 0.1585 / 0.1597。
  两者都没有加载 Alignment。

复测：至少循环三次 `/usr/bin/time -p env PYTHONPATH=backend .venv/bin/python -c
'import pianokt_backend.api'`，Relay 将模块名换成 `pianokt_backend.outbox_dispatch`。
`python -X importtime` 可进一步定位 import，不能替代真实请求冷启动测量。

待测：部署环境 finalize p50/p95 和直发附加延迟、真实 Worker 子进程总耗时及其中初始化
比例、Cloud Run 冷实例延迟、Relay Job 空跑时间/峰值内存、镜像拉取耗时。模拟 Publisher
和本地 import 的结果不能冒充这些数据。

## 观测、费用假设与回滚

- `http.finalize`：handler 内主事务和派发耗时；外层 `http.POST.finalize` 还包括认证/框架。
- `outbox.transaction`：提交后的派发连接/事务；`pubsub.publish`：Publisher 初始化及 ACK；
  `outbox.confirm`：确认 UPDATE/COMMIT。不要将第二个 db.transaction 当成主 Finalize 事务。
- 每次确认输出 event_id/request_id，失败只输出错误类型/阶段；没有 token、URL、SQL 参数
  或 payload。`worker.duplicate` 按 terminal/active_lease/retry_deferred 计数，
  `worker.subprocess.total` 记录每个子进程总时间，`outbox.relay` 记录实际确认数量。
- 用 `backend/diagnostics/relay_health.sql` 监控最老 pending age、待发条数、到期 RETRY、
  过期 lease、FAILED、缺失 Outbox 的 ACCEPTED；为积压和周期性 Job 失败设告警。
- 按规格的计费假设：30 天、每 15 分钟一次共 2,880 次、每次按 60 秒计费且没有重试，
  1 CPU/1Gi 对应每月各 172,800 CPU 秒/GiB 秒。注意线上当前是每天两次，目标每天 96 次，
  因此相对当前线上调度会增加运行次数；“低频”是相对于源码原先每分钟的设计。只有结算账号仍有相应免费额度、实际任务
  不超出该运行量时，Relay 才可能落入免费范围；不承诺整体产品 $0。实际 Job 时长、
  重试、Logging、Pub/Sub、网络、镜像及其他服务费用以 Billing 为准，设置预算提醒。
- 回滚：关闭直发开关，并将 Relay 临时恢复为每 1–5 分钟或人工运行一次；确保兼容消息的
  Worker 订阅仍在。保留 Outbox、Attempt、DLQ 和 017，镜像只能回到兼容 finalize v3 的版本。
