# PianoKT：运行、上云与下一步蓝图

这是一份可操作的交接手册。云资源配置尚未在你的账户 apply；下列云端命令由你在目标项目中执行。本地验证结果单独记录在 `VALIDATION_ZH.md`，不要把本地测试通过理解为云端已经联调通过。

## 1. 最快看见真实 alignment 与 Delta 表

在解压后的 `pianokt` 根目录执行。推荐 Python 3.11；后端支持 3.11–3.12，交付锁文件在 3.11 验证。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.lock
python -m pip install --no-deps -e backend
python -m pytest backend/tests -q
python scripts/make_demo_midis.py
pianokt-data align \
  --reference data/demo/reference.mid \
  --performance data/demo/performance.mid \
  --attempt-id demo-two-hands \
  --raw data/raw \
  --lake data/lakehouse
```

预期得到原始两份 MIDI、matching 阶段文件、alignment result.json，以及本地 Bronze/Silver/Gold Delta 目录。合成演奏包含整体 500 ms 延迟；测试用来证明管道与对齐接口工作，不代表真实学生上的准确率验证。

重放同一批已经生成的结果：

```bash
RAW_ROOT=data/raw LAKE_ROOT=data/lakehouse pianokt-data pipeline
RAW_ROOT=data/raw LAKE_ROOT=data/lakehouse pianokt-data pipeline --replay
```

第一次普通重跑应发布 0 个新 run。`--replay` 会重新 MERGE 已有 run，不应把音符数翻倍。这不是重新运行新版本 matcher 的命令；新算法版本应另行运行 alignment，并保留不同 run ID。

读取本地表：

```bash
python - <<'PY'
from deltalake import DeltaTable
path='data/lakehouse/gold/fact_practice_attempt'
print(DeltaTable(path).to_pyarrow_table().to_pandas())
PY
```

实际业务查询还要遵循教学文档里的 completed_runs 发布协议。

## 2. 本地验证 Spark AvailableNow

需要 Java 17。不要把 Spark 放到在线 API 请求中。

```bash
python -m pip install 'pyspark==4.0.1' 'delta-spark==4.0.0'
bash backend/fetch-jars.sh /tmp/pianokt-jars
PIANOKT_TEST_SPARK=1 \
SPARK_LOCAL_IP=127.0.0.1 \
PIANOKT_SPARK_JARS=/tmp/pianokt-jars/delta-spark.jar,/tmp/pianokt-jars/delta-storage.jar,/tmp/pianokt-jars/gcs.jar \
PYSPARK_SUBMIT_ARGS='--master local[2] pyspark-shell' \
python -m pytest backend/tests/test_spark.py -q
```

此测试真正启动 Spark：写入一条有效事件与一条坏 JSON，检查 Bronze 和隔离表；然后添加第二个文件，使用同一个 checkpoint 重启，检查 Silver 的行数。GCS connector 被装入类路径，但本测试使用本地目录，不能证明目标 GCS 的 IAM、区域网络与凭据已正确。

## 3. 前端运行

```bash
npm ci --ignore-scripts
cp .env.example .env.local
# 编辑 .env.local，填写 Supabase 公共配置与后端 API 地址。
npm run check-types
npm run dev
```

部署静态前端时运行 `npm run build`，产物位于 `build/client`。Vite 环境变量在构建时注入，因此修改 API URL 后必须重新构建。绝不能把 PostgreSQL 密码或 Supabase service-role key 放入 `VITE_*`。

`--ignore-scripts` 适用于这里验证的前端构建路径。原工程的可选媒体渲染脚本使用额外原生依赖；如果要运行原来的 `scripts/render.ts`，需要按其依赖补装运行环境，这次没有验证它。

## 4. Supabase 数据库准备

已有部署应先确认迁移 000–008 与实际数据库一致，然后只应用新增 009。不要因为拿到新工程，就把已有数据库的所有迁移盲目重跑。空项目需要按原迁移的依赖顺序安装旧 schema，再运行 009。

迁移 009 增加 attempt、learner mapping、outbox、推荐和偏好等表；没有改写旧迁移历史，也没有搬迁历史录音文件。

后端通过 PostgreSQL 连接串访问数据库。推荐创建专用登录角色，不使用数据库超级用户长期运行服务：

1. 管理员运行 `supabase/backend_role.sql`，建立无登录的权限组与明确 RLS policy。
2. 创建一个独立 LOGIN 角色，赋予 `pianokt_backend` 组成员身份。
3. 用密码管理流程设置密码；使用 psql 时可用 `\password 登录角色名`，避免把密码写进工程或 shell 历史。
4. 将该角色的连接串放入 Secret Manager。

当前四个后端进程共用这个专用权限组；它仅获授相关表访问权限，未授予 BYPASSRLS 或 superuser。后续可以再细分 API / Worker / Relay / Pipeline 数据库角色。不要把此后端组授予 `anon` 或 `authenticated`。

选择 Supabase 直连或 **session pooler**。pipeline 使用会话级 advisory lock，不能接 transaction pooler。需要兼顾 Cloud Run 的网络可达性与 Supabase 提供的 IPv4/IPv6 接入方式；直接照抄主机名不能保证网络可达。连接串使用 `sslmode=require`，严谨生产环境可根据服务端证书配置进一步使用 verify-full。

开发演示可运行 `supabase/seed_demo_catalog.sql`，其中三首歌曲在原工程 `public/music/songs/` 存在。难度数字只是演示标签，尚未经过学生数据校准。偏好写入 `piano_preferences`，已有 RLS 只允许学生修改自己的偏好；当前没有新建完整偏好编辑页面。

## 5. GCP 部署顺序



### 5.1 准备变量、身份和远程 state

以下使用独立 PianoKT 资源名，不改已有 commerce-lakehouse 资源。区域示例继续使用 `us-central1`；最终还需根据数据库位置、访问延迟和数据管理要求选择。

```bash
export PROJECT_ID='你的项目ID'
export REGION='us-central1'
export VERSION='pianokt-v1'
gcloud auth login
gcloud auth application-default login
gcloud config set project "$PROJECT_ID"
gcloud services enable storage.googleapis.com artifactregistry.googleapis.com \
  run.googleapis.com pubsub.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com
```

为 Terraform 创建专用 state bucket，名称必须全局唯一。已存在时应检查它的归属和配置，而不是另建同名资源：

```bash
gcloud storage buckets create "gs://${PROJECT_ID}-pianokt-tfstate" \
  --location="$REGION" --uniform-bucket-level-access
gcloud storage buckets update "gs://${PROJECT_ID}-pianokt-tfstate" --versioning
cp infra/gcp/terraform.tfvars.example infra/gcp/terraform.tfvars
# 编辑 tfvars：project、region、镜像、前端域名、Supabase 公共配置、secret ID。
terraform -chdir=infra/gcp init \
  -backend-config="bucket=${PROJECT_ID}-pianokt-tfstate" \
  -backend-config='prefix=pianokt/dev'
```

state bucket 不在主模块中创建，避免“为了创建保存 state 的 bucket，先需要一个保存 state 的 bucket”的循环依赖。tfstate 包含基础设施细节，不应进入下载包或 Git。

### 5.2 创建 secret 与镜像仓库

```bash
gcloud secrets create pianokt-database-url --replication-policy=automatic
# 将真实连接串保存在仅当前用户可读的临时文件中，再提交 secret version。
gcloud secrets versions add pianokt-database-url --data-file='/安全路径/database-url.txt'
```

这一步不由 Terraform 保存 secret 内容，从而避免连接串进入 Terraform state。secret 创建和 IAM 权限需要由目标项目有权限的部署身份执行。

第一次部署时，镜像仓库必须先于镜像构建存在。可仅在首次初始化时针对仓库资源执行：

```bash
terraform -chdir=infra/gcp apply -target=google_artifact_registry_repository.images
```

这是一次明确的 bootstrap 步骤，后续正常发布使用完整 plan/apply，不长期依赖 `-target`。此时 tfvars 的镜像地址可以先填将要构建的版本地址；仓库 bootstrap 不会创建 Cloud Run 服务。

### 5.3 构建两个镜像

在工程根目录、具备 Docker Buildx 的本地机器执行。Mac 上显式构建 linux/amd64，避免把仅能在 ARM 本机运行的镜像交给云端。

```bash
gcloud auth configure-docker "${REGION}-docker.pkg.dev"
docker buildx build --platform linux/amd64 \
  -f backend/Dockerfile \
  -t "${REGION}-docker.pkg.dev/${PROJECT_ID}/pianokt/backend:${VERSION}" --push .
docker buildx build --platform linux/amd64 \
  -f backend/Dockerfile.spark \
  -t "${REGION}-docker.pkg.dev/${PROJECT_ID}/pianokt/pipeline:${VERSION}" --push .
```

普通镜像运行 API、Worker 和 relay；Spark 镜像额外含 Java、Spark、Delta JAR 与固定版本 GCS connector。JAR 在构建时下载，作业启动时不再临时访问 Maven。交付环境没有 Docker daemon，因此这两个镜像仍需你在目标构建环境实际构建验收。稳定上线时应把 tfvars 镜像 tag 换成确定 digest。

### 5.4 完整创建服务

```bash
terraform -chdir=infra/gcp validate
terraform -chdir=infra/gcp plan -out=pianokt.tfplan
terraform -chdir=infra/gcp apply pianokt.tfplan
terraform -chdir=infra/gcp output -raw api_url
```

将 api_url 填入前端构建配置。Terraform 创建两类计算：


| 资源                       | 触发方式              | 默认并发/频率                         |
| ------------------------ | ----------------- | ------------------------------- |
| `pianokt-api` Service    | 浏览器请求             | 每实例并发 20，最多 3 实例                |
| `pianokt-worker` Service | Pub/Sub OIDC push | 每实例并发 1，最多 3 实例                 |
| `pianokt-relay` Job      | Scheduler 或手动     | 每天日本时间 02:00，单次最多发布 100 条       |
| `pianokt-pipeline` Job   | Scheduler 或手动     | 每天日本时间 03:00，单任务、Spark local[2] |


API 的 Cloud Run invoker 允许公开访问，但业务端点校验 Supabase token。Worker **不能**公开：它依赖 Cloud Run IAM 验证 Pub/Sub push 身份。若你绕开 Cloud Run 把 worker_api 直接暴露到公网，应用本身没有独立验证 OIDC 的中间件，会破坏这个安全边界。

调度器默认 paused=true，便于完成首轮验收再开启。运行后的资源会产生费用；当前 relay 与 pipeline 均为每日一次，需要观察事件量、单次处理容量、数据延迟、存储操作和网络费用，再调整频率。

## 6. 云端第一轮验收

按以下顺序验证，不要只看 `/health` 返回 200：

1. 用户 A 登录前端，完成至少三个不同音符时刻的正常练习。
2. 浏览器请求里出现 `POST /attempts`、两次 GCS PUT、finalize；不存在新录音的 Supabase Storage upload。
3. GCS 两个 MIDI 均存在，未登录者无法直接读取私有对象；重复 PUT 返回 412，合法 finalize 仍可完成。
4. 手动执行 relay：

```bash
gcloud run jobs execute pianokt-relay --project="$PROJECT_ID" --region="$REGION" --wait
```

1. Worker 被 Pub/Sub 触发，状态变为 READY；记录页能下载 alignment JSON。
2. 用户 B 的 token 请求 A 的 attempt/status/download 应返回 404。无 token 不应访问推荐或上传接口。
3. 手动执行 pipeline，然后检查 Delta completed manifest 和各层数据：

```bash
gcloud run jobs execute pianokt-pipeline --project="$PROJECT_ID" --region="$REGION" --wait
```

1. 再执行 pipeline，确认同一 run 的行数不增长。检查故意损坏的测试 JSONL 进入 Bronze 与 quarantine。
2. 配置演示曲目目录，测试推荐、曝光/点击反馈和事件归档。确认界面仍显示演示模型提示。
3. 验收通过后，在 tfvars 中设置 `schedules_paused=false`，重新完整 plan/apply。

Cloud Scheduler 调用 Job API 成功，只意味着执行请求被接受，不表示内部计算成功。应看 Cloud Run execution 的最终状态和应用日志。

## 7. 常见故障与恢复


| 现象                  | 首先检查                                                | 恢复方向                         |
| ------------------- | --------------------------------------------------- | ---------------------------- |
| GCS PUT 403         | 签名内容、Content-Type、generation 条件头、API 自身 signBlob 权限 | 修正配置后对同一 attempt 重试          |
| 浏览器 CORS 失败         | 原始 bucket 的 exact frontend origin，API allow_origins | 修正域名配置并重新部署                  |
| finalize 409        | 上传是否完成、SHA 是否相符                                     | 不绕过核验，检查两份文件                 |
| UPLOADED 长时间不动      | outbox pending、relay 调度与 publish 权限                 | 执行 relay，检查 Pub/Sub push     |
| PROCESSING 长时间不动    | Worker 日志、lease、超时                                  | 等待 lease 过期，重投/重试            |
| InsufficientAnchors | 是否只有一个和弦或极短演奏、配对质量                                  | 检查原始和 matching 文件，不能伪造正常时序分数 |
| FAILED 反复出现         | dead-letter 队列与具体异常                                 | 修复原因后由管理员运行 worker 命令        |
| READY 但没有 Gold      | pipeline 调度、quality、ops/failures                    | 修复后重放 result.json            |
| Spark 无法识别 gs://    | 镜像 JAR、服务账号、connector 配置                            | 用实际云端输入验证，不换成公共 bucket       |
| Pipeline 锁一直忙       | 是否多个执行重叠、是否用了 session pooler                        | 检查运行任务与数据库会话                 |
| 推荐为空                | 歌曲目录为空或 disabled                                    | 导入真实候选曲目                     |


管理员在具备后端环境变量与 GCS ADC 的运行环境可执行 `pianokt-data worker <attempt_uuid>` 处理 FAILED 或过期 PROCESSING 任务。READY 的同版本任务会跳过。新算法的批量重新对齐需要额外的版本发布/选择流程，不应通过删除原始文件或修改同一 immutable run 来实现。

分析表重建使用新 `LAKE_ROOT`，重新读取保留的 result.json。事件回填则使用单独 checkpoint 与输入前缀。`--replay` 是重复物化已有 alignment 结果，不负责删除 Spark checkpoint，也不会重发 Pub/Sub 历史消息。

## 8. 训练导出与权限

```bash
RAW_ROOT='gs://你的项目-pianokt-raw' \
LAKE_ROOT='gs://你的项目-pianokt-lake' \
pianokt-data export-training \
  --cutoff='2026-10-01T00:00:00Z' \
  --output-key='datasets/experiment-001.json'
```

执行身份需要读取 raw/lake，并创建 `datasets/` 对象。本版普通 pipeline 服务账号仅获授 raw 读取权限，训练导出应使用独立的授权训练身份，或增加仅针对 datasets 前缀的对象创建权限。它不是默认每 15 分钟自动执行的步骤。

本地合成 `align` 命令没有真实学生时间上下文，故不会自动进入训练导出。训练快照应来自真实 Worker 上下文或明确构造的测试 fixture。

## 9. 接下来做到“正式完整产品”的蓝图


| 优先级 | 下一步                   | 完成标准                                              |
| --- | --------------------- | ------------------------------------------------- |
| P0  | 在隔离测试项目执行上述云端验收       | A/B 用户隔离、签名上传、重试、死信、入湖全部有记录                       |
| P0  | 建立权威曲谱版本和练习定义         | 每个 assignment 对应服务端可验证 score hash、手部与范围配置         |
| P0  | 收集真实对齐评估集             | 包含正确、错音、多音、缺音、停顿、片段、单手、等待模式；人工标注并报告各类错误           |
| P0  | 账户撤回/删除与保留流程          | 能关联删除或隔离 GCS、Delta、导出与在线记录；处理 Delta 历史版本和备份       |
| P1  | 可观测性和容量验收             | 有 outbox 年龄、Worker 失败率、耗时、pipeline lag、坏数据率和预算告警  |
| P1  | 扩展 relay 吞吐和在线延迟      | 按实测事件量调 batch/频率；需要更低延迟时改为合适的常驻或事件触发 relay        |
| P1  | 完整曲目/偏好管理             | 曲目来源、乐谱版本、difficulty provenance、偏好 UI；演示目录被真实目录替代 |
| P1  | 训练与评估管道               | 学生/时间拆分、无泄漏窗口、固定数据版本、模型注册、与基线比较                   |
| P1  | 接入真正推荐模型              | 输入兼容完整 alignment，输出校准的预测和模型版本，移除 demo 标识前完成验收     |
| P2  | 整曲推荐目标与产品实验           | 同时测预测质量、能力匹配、完成率、学习收益；曝光和点击只是其中部分信号               |
| P2  | 扩容湖仓和查询入口             | 文件压缩整理、分区与增量发现优化；按规模迁移 Spark 运行环境                 |
| P2  | BigQuery/BigLake 只读分析 | 若需要，注册兼容的 Delta 外表；不要求复制 Gold 到托管表                |


当前用单机 Spark + delta-rs 面向早期规模，不承诺 TB 级吞吐；每个小 run 多次 MERGE 会产生小文件，`catch_up` 也会扫描结果列表。增长后要加入批量物化、compaction、目录分区与队列/清单索引。保留清楚的数据契约后，这些改动可以主要发生在分析后端，不用重写播放器。

数据伪名化、RLS 和私有 bucket 并不自动完成研究数据管理要求。工程没有自动删除用户的湖仓历史，也没有擅自设定所有数据的保留期限。具体保留、撤回、备份与研究导出应按你的项目要求实施，而不是用一个粗糙的生命周期规则误删原始研究数据。