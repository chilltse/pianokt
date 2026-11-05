# PianoKT 运行与部署手册

2026-11-05 最新架构：异步 finalize v3 + migration 017，详见 [异步接收与部署](ASYNC_FINALIZATION_ZH.md)。此前性能优化见 [计时说明](UPLOAD_PERFORMANCE_ZH.md)。

更新日期：2026-11-04。当前异步提交先按 [017 协调升级顺序](ASYNC_FINALIZATION_ZH.md) 执行；下文保留 migration 016 和 infra/gcp/main.tf。命令是供目标环境执行的操作说明；本次文档整理没有部署云资源。系统分工见 [架构总览](ARCHITECTURE_ZH.md)。

当前默认 `--source direct --environment production`。完整结构见 [v2 数据字典](DATA_CONTRACT_V2_ZH.md)。以下升级步骤优先于文中保留的 014 历史升级说明。

## 历史 v2 / migration 016 升级顺序与注意事项

1. 安排一次协调升级窗口，停止旧前端创建新演奏，暂停旧 API/Worker 的写入；本轮不维护 v1 上传兼容层。
2. 在已有 001–015 的数据库执行 **新增** `supabase/migrations/016_challenge_event_authority.sql`。不要重跑整套历史 migration，不修改已应用文件。它新增来源/封存/投影字段，关闭旧 RPC/trigger，不删除历史数据。
3. 发布新 API 和 Worker。生产明确设置 `PIANOKT_ENV=production`；开发使用独立 Supabase/GCS 和 development。未设置默认 development，因此漏配会使正式导出/榜单为空，而不会把开发数据当生产数据。
4. 发布新前端并刷新所有标签页。新队列为 `pianokt-upload-recovery-v2`。旧 v1 请求返回 422，旧 IndexedDB/localStorage 队列不转换、不删除、不自动重试。当前允许不保留旧演奏的项目可直接新录；不要把本说明理解为自动清库。
5. 检查 GCS CORS 和签名凭据。Terraform 默认 local_development_origins=[]，开发应使用开发部署；不要给生产加入 localhost 作为长期测试方案。
6. 执行 [保存验收](CHALLENGE_SAVE_VERIFICATION_ZH.md)，确认只写 student_attempt_events，原始表无新增，outbox每attempt一条performance.uploaded。
7. `schedules_paused=false` 才启用默认调度；`lake_pipeline_paused=true` 保持可选 Lake 独立暂停。新主链路需要 relay 和 Worker，不需要 Spark。现有 Terraform 状态不会因代码改动自动改变，请审阅 plan 再 apply。
8. 导出正式数据使用 `--environment production`（默认）；开发验证显式 development。历史行被标为 unknown，默认排除；不可简单批量标 production，需独立审计。

本迁移与新后端是一组部署单位。应用 016 后直接回滚旧 API 会遇到原始事件写权限和协议不兼容；优先向前修复，若要回滚需要专门迁移/版本协调。投影修复可运行：

```bash
pianokt-data rebuild-projections --attempt-id UUID
pianokt-data export-attempts --cutoff 2026-11-05T00:00:00Z --output-prefix datasets/production-v2-001
pianokt-data export-attempts --environment development --cutoff 2026-11-05T00:00:00Z --output-prefix datasets/dev-v2-001
```

上面的 cutoff 是示例，需要按实际研究边界选择。已有输出前缀不可用于不同内容的重新发布。实际部署不在本次代码修改中自动执行。

## 1. 先选择运行路径

- 网页和本地 Lab：见 [本地开发](LOCAL_DEVELOPMENT_ZH.md)。
- 离线合成 MIDI → 对齐 → Delta：用下面的 CLI 示例。
- 真实账号 → GCS 保存 → worker → 入湖：需要 Supabase schema、GCP 配置、签名身份与外部凭据。

前端、普通后端、Spark pipeline 是独立启动入口。运行 `npm run dev` 不会自动启动整套后台。

## 2. 离线算法与物化示例

Python 使用 3.13.12，在根目录创建环境：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.lock
python -m pip install --no-deps -e backend
python scripts/make_demo_midis.py
pianokt-data align \
  --reference data/demo/reference.mid \
  --performance data/demo/performance.mid \
  --attempt-id demo-two-hands \
  --raw data/raw \
  --lake data/lakehouse
```

align 会保留两份 MIDI、matching 阶段 JSON、完整 result，并执行 alignment 的 Delta 物化。合成输入带整体延迟，用来验证实现，不代表真人评估集。

这个命令没有真实 student visit/attempt/注册时间，也没有 worker 写入的完整业务时间元数据。不要据此期待 export-attempts 自动生成真实训练样本。

重跑与读取：

```bash
RAW_ROOT=data/raw LAKE_ROOT=data/lakehouse pianokt-data pipeline
RAW_ROOT=data/raw LAKE_ROOT=data/lakehouse pianokt-data pipeline --replay
python - <<'PY'
from pianokt_backend.pipeline.tables import Tables
snapshot = Tables('data/lakehouse').snapshot([
    'gold/fact_midi_alignment', 'gold/fact_alignment_attempt'
])
for name, rows in snapshot.items():
    print(name, len(rows))
PY
```

普通重跑跳过已完成 run；--replay 重新遍历并校验，当前已完成 run 保持不可变，不会重新 MERGE 改写旧成果。物化业务规则变更应使用新 LAKE_ROOT 重建。

## 3. Spark 事件流验证

需要 Java 17、Spark 4.0.1、Delta 4.0.0：

```bash
python -m pip install 'pyspark==4.0.1' 'delta-spark==4.0.0'
bash backend/fetch-jars.sh /tmp/pianokt-jars
PIANOKT_TEST_SPARK=1 \
SPARK_LOCAL_IP=127.0.0.1 \
PIANOKT_SPARK_JARS=/tmp/pianokt-jars/delta-spark.jar,/tmp/pianokt-jars/delta-storage.jar,/tmp/pianokt-jars/gcs.jar \
PYSPARK_SUBMIT_ARGS='--master local[2] pyspark-shell' \
python -m pytest backend/tests/test_spark.py -q
```

测试检查原始/坏事件保留、同 checkpoint 恢复、有效事件提交及重复 ID 异内容冲突。它采用本地目录，不验证目标 GCS IAM。

Spark 输入是 Pub/Sub 已归档的 raw/events。alignment result 的 Delta 物化由 jobs.py + delta-rs 执行，不要求每个结果都经过 Spark DataFrame。

## 4. 浏览器配置与构建

```bash
npm ci --ignore-scripts
cp .env.local.example .env.local
# 填写公共 Supabase 配置与 VITE_PIANOKT_API_URL。
npm run check-types
npm run dev
```

正式保存路径关闭 VITE_LOCAL_ALIGNMENT_LAB。静态产物由 `npm run build` 生成至 build/client，前端托管另行配置；当前 Terraform 不创建前端静态站。

VITE 环境变量在构建时写入浏览器包，API 地址变化后重新构建。数据库连接串、私钥和 service-role key 不进入 VITE_*。

## 5. Supabase schema 与权限

已有数据库先确认实际迁移记录及 schema，再应用缺少项。不能把所有 SQL 文件盲目重跑：很多建表/建策略脚本不是幂等的。

正式 schema 依赖包括：

1. 001–008：历史录音、访问策略、profiles、播放事件与汇总。
2. 009：piano_attempts、learner key、outbox、推荐、目录/偏好。
3. 010：backend 写 cycle 的权限补充。
4. 011：performance-events sidecar 字段与一致性约束。
5. 012：日本时间查询视图。
6. 013：student_visits、student_attempts、student_attempt_events 与注册时间查询。
7. 014：finalization、lease_token、正式评分来源、旧事件原子 RPC 与权限调整。

000 是历史修复脚本，sss 是临时查询，不在正式顺序中。空 Supabase 项目也需先确认 auth/storage/profiles 等依赖，不把这些脚本当成独立 PostgreSQL 空库的一键初始化器。

后端使用专用登录角色：管理员按 `supabase/backend_role.sql` 建 pianokt_backend 权限组，另建 LOGIN 角色并授予成员身份。运行角色没有 BYPASSRLS/superuser，不授予浏览器 anon/authenticated。用安全密码流程配置连接串，交给 Secret Manager。

若权限组在 013 后首次建立，backend_role.sql 包含活动表的补充授权；若先建组，再应用迁移，013 会按角色存在情况追加权限。已有角色不要重跑 CREATE ROLE 脚本导致升级中途报错。

pipeline 使用 session advisory lock，选择 Supabase 直连或 session pooler；transaction pooler 不适合这条锁语义。连接使用 TLS，并核对目标网络可达性。

演示推荐目录可运行 supabase/seed_demo_catalog.sql。目录难度是演示配置，偏好表已有 RLS，但尚未提供完整偏好编辑 UI。

## 6. GCP 实际资源

infra/gcp/main.tf 定义：

- Cloud Run Service：pianokt-api、pianokt-worker。
- Cloud Run Job：pianokt-relay、pianokt-pipeline。
- 私有桶：`<project>-pianokt-raw`、lake、checkpoint。
- Pub/Sub：主 topic、全事件 GCS 归档订阅、仅上传事件的私有 worker push 订阅、死信 topic/检查订阅。
- 六种服务账号：api、worker、relay、pipeline、scheduler、push。
- Artifact Registry、Secret Manager 访问授权、必要 IAM 和两项 Scheduler 配置。

API 是可公开调用的 Cloud Run 服务，业务请求仍校验 Supabase 身份。worker 仅授权 push 身份调用；不要把 worker_api 单独公开到公网。

默认 API 每实例并发 20，worker 并发 1，各最多 3 实例；Job 单任务、单并行度。pipeline 镜像使用 Spark local[2]，不是 Dataproc 集群。

## 7. 首次部署顺序

以下是运行说明，不应把计划文件或数据库凭据提交 Git。

### 7.1 目标变量和远程状态

```bash
export PROJECT_ID='你的项目ID'
export REGION='us-central1'
export VERSION='pianokt-v1'
gcloud auth login
gcloud auth application-default login
gcloud config set project "$PROJECT_ID"
gcloud services enable storage.googleapis.com

gcloud storage buckets create "gs://${PROJECT_ID}-pianokt-tfstate" \
  --location="$REGION" --uniform-bucket-level-access
gcloud storage buckets update "gs://${PROJECT_ID}-pianokt-tfstate" --versioning
cp infra/gcp/terraform.tfvars.example infra/gcp/terraform.tfvars
terraform -chdir=infra/gcp init \
  -backend-config="bucket=${PROJECT_ID}-pianokt-tfstate" \
  -backend-config='prefix=pianokt/dev'
```

先编辑 tfvars 中 project、region、image、spark_image、frontend_origin、Supabase 公共配置与 database_secret_id。state bucket 由引导流程创建，不包含在主 Terraform 模块中。

### 7.2 数据库 secret 与镜像仓库

```bash
gcloud secrets create pianokt-database-url --replication-policy=automatic
gcloud secrets versions add pianokt-database-url --data-file='/安全路径/database-url.txt'
terraform -chdir=infra/gcp apply -target=google_artifact_registry_repository.images
```

secret 内容不通过 Terraform 变量保存。target 仅用于首次引导镜像仓库，正常发布用完整 plan/apply。

### 7.3 构建两种镜像

```bash
gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker buildx build --platform linux/amd64 \
  -f backend/Dockerfile \
  -t "${REGION}-docker.pkg.dev/${PROJECT_ID}/pianokt/backend:${VERSION}" --push .
docker buildx build --platform linux/amd64 \
  -f backend/Dockerfile.spark \
  -t "${REGION}-docker.pkg.dev/${PROJECT_ID}/pianokt/pipeline:${VERSION}" --push .
```

普通镜像运行 API/worker/relay；Spark 镜像增加 Java、Spark、Delta 与 GCS JAR。两个镜像均固定 Python 3.13.12。正式发布应固定确定的镜像版本或 digest。

### 7.4 创建资源并构建前端

```bash
terraform -chdir=infra/gcp validate
terraform -chdir=infra/gcp plan -out=pianokt.tfplan
terraform -chdir=infra/gcp apply pianokt.tfplan
terraform -chdir=infra/gcp output -raw api_url
```

将输出 API 地址用于前端构建。检查 API CORS 与 raw bucket CORS 均包含实际 frontend origin；Terraform 也包含两个 loopback 开发 origin 的配置入口。

## 8. 调度、延迟与手动执行

Scheduler 时区固定 Asia/Tokyo：

- relay_schedule 默认 `0 10,23 * * *`，即 10:00/23:00。
- pipeline_schedule 默认 `0 0,11 * * *`，即 00:00/11:00。
- schedules_paused 默认 true，先验收再启用。

默认两者都是每日两次。CLI relay 默认最多 1000 条 outbox，并不保证一次清空积压。互动产品需要更低延迟时，可配置更高频 relay，并观察 backlog/耗时；暂停配置、Pub/Sub 重试和任务容量也影响时效。

手动执行：

```bash
gcloud run jobs execute pianokt-relay --project="$PROJECT_ID" --region="$REGION" --wait
gcloud run jobs execute pianokt-pipeline --project="$PROJECT_ID" --region="$REGION" --wait
```

Scheduler 请求成功不代表内部作业计算成功，应核对执行状态与应用日志。API 请求不等待下一次 pipeline，worker 完成后就能通过 API 读取在线结果。

## 9. 历史记录：014 版本的协调升级（当前升级以本文开头 016 为准）

既有部署升级时，先备份并准备应用回滚方案；暂停/排空旧 worker，再应用缺少的 013、014，随后部署新 API/worker 和前端，最后恢复调度。

原因有两个：旧 worker 不遵守新 lease_token 发布协议；014 撤销旧裸写/RPC 权限，旧客户端可能需要刷新。不能声称这一版可以任意顺序零停机混跑。

湖仓正式 reader 依赖新完成标记。Spark 历史已被 checkpoint 消费的数据不会自动补 practice_event_commits，采用新 lake/checkpoint 路径重放验收后切换。对齐物化语义变化同样采用新 LAKE_ROOT，保留旧版本。

回滚时先停止写入，选择一套相互匹配的应用/权限；不要通过删列、覆盖 raw 或删除历史文件回滚业务数据。

## 10. 第一轮实际验收

1. 已登录学生完成正常 Challenge，检查 /activity、/attempts、三份文件 PUT、/finalize。
2. 同一 attempt 断网重试/响应丢失重试后，最终录音不重复；切账号不误传原用户包。
3. 原始对象不可公开读取；用户 B 请求用户 A 的状态/下载返回 404。
4. 运行 relay，观察 worker 到 READY，区分质量有效与拒绝；用 API 下载 result，而不是假设录音页已有 result 按钮。
5. 运行 pipeline，检查 ops 完成标记与正式 Silver/Gold；坏事件进入 quality，同 ID 异内容被拒绝。
6. 查看正式 aligned 分数与排行榜；配置目录后验证演示推荐及反馈。
7. 以真实上下文导出一个新 prefix，使用 read_bundle 验证文件集合。
8. 通过后再恢复 Scheduler，并记录镜像、migration、配置与验收结果。

录音页现有 UI 包含 MIDI 下载和演示推荐，没有完整后端状态/对齐结果查看器。

## 11. 训练导出身份与命令

导出是管理员/研究流程，不是浏览器端 API，也没有自动定时训练。

```bash
RAW_ROOT='gs://你的项目-pianokt-raw' \
LAKE_ROOT='gs://你的项目-pianokt-lake' \
pianokt-data export-attempts \
  --cutoff='2026-11-03T00:00:00Z' \
  --output-prefix='datasets/experiment-001'
```

命令还需要 DATABASE_URL。执行身份须能读活动、注册时间、learner/attempt/汇总上下文和 raw/lake；创建 datasets 对象需要额外授权。当前普通 pipeline 服务账号只有 raw 读取权限，不能据此推断它能向 raw/datasets 写导出。

同一 attempt 多个候选时加 `--run-selection selection.json`。逐 onset 输出则用 export-training + --output-key。字段和发布规则见 [导出说明](VISITS_AND_ATTEMPT_DATASET_ZH.md)。

## 12. 故障与恢复入口

- CREATED：查 IndexedDB 包、签名/PUT、finalize 错误；不是先手工改状态。
- UPLOADED：查 outbox pending、relay、Pub/Sub push。
- PROCESSING：查租约、worker 日志和超时；过期租约可重新领取。
- FAILED：查 error_code、原始输入和死信；管理员可运行 worker UUID 重试。
- READY 无可信分数：查质量原因，不能手工填零掩盖。
- READY 无 Gold：查 pipeline、quality、ops/failures/completed。
- 导出版本冲突：指定 run 或新 prefix，不覆盖已发布文件。
- pipeline writer busy：检查其他运行实例和数据库连接池类型。

当前没有全局永久删除/保留策略实现、生产监控面板、自动模型训练或服务端权威乐谱 assignment 校验。这些是后续能力，不能在当前架构图中画成已存在的服务。
