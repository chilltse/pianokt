# PianoKT 系统全景参考手册

> 本文档是自包含的。读完这一份，你应该能回答：一个用户在浏览器里弹了一首曲子之后，
> 这段演奏变成了多少份数据、分别存在哪里、经过了哪些程序、最终长成什么样子，
> 以及每一个配置项如果填错了会发生什么。
>
> 每一章开头有一段「一句话版本」，用日常语言说明这一章在讲什么。之后是技术细节。
>
> 文档基于代码版本：backend `0.8.1`、pipeline 镜像 `0.8.2`、migration 至 `009`。

---

## 目录

1. [全景图：一次演奏的完整旅程](#第-1-章-全景图一次演奏的完整旅程)
2. [配置总清单](#第-2-章-配置总清单)
3. [数据是怎么产生的：浏览器端](#第-3-章-数据是怎么产生的浏览器端)
4. [两条并行的写入链路](#第-4-章-两条并行的写入链路)
5. [上传三段式](#第-5-章-上传三段式)
6. [Outbox 与 Relay](#第-6-章-outbox-与-relay)
7. [Pub/Sub 的三个订阅](#第-7-章-pubsub-的三个订阅)
8. [对齐是怎么算的](#第-8-章-对齐是怎么算的)
9. [GCS 每一层路径](#第-9-章-gcs-每一层路径)
10. [Bronze / Silver / Gold](#第-10-章-bronze--silver--gold)
11. [权限与身份](#第-11-章-权限与身份)
12. [故障排查手册](#第-12-章-故障排查手册)

---

# 第 1 章 全景图：一次演奏的完整旅程

## 一句话版本

用户弹琴时，系统同时做两件事：一是记录「他在什么时候做了什么操作」（开始、暂停、退出），
二是录下「他到底按了哪些琴键」。前者立刻算成一行学习记录，后者要送到云端跟标准答案逐个音符比对，
比对结果再被整理成可以用来做分析和推荐的数据表。

## 1.1 参与者清单

整个系统由 4 个存储系统和 7 个计算单元组成。

**存储系统：**

| 名称 | 技术 | 存什么 |
|---|---|---|
| Supabase Postgres | 托管 PostgreSQL | 用户、录音元数据、行为事件、任务状态、消息队列 |
| GCS raw 桶 | 对象存储 | MIDI 原始文件、对齐结果 JSON、事件归档 |
| GCS lake 桶 | 对象存储 + Delta Lake | Bronze/Silver/Gold 分析表 |
| GCS checkpoint 桶 | 对象存储 | Spark 流式作业的断点位置 |

**计算单元：**

| 名称 | 形态 | 触发方式 | 职责 |
|---|---|---|---|
| 浏览器前端 | React SPA（Vercel） | 用户操作 | 采集演奏、生成 MIDI、调用 API |
| `pianokt-api` | Cloud Run Service（公开） | 前端 HTTPS 调用 | 鉴权、建 attempt、发签名 URL、校验上传 |
| `pianokt-worker` | Cloud Run Service（私有） | Pub/Sub 推送 | 执行 MIDI 对齐 |
| `pianokt-relay` | Cloud Run Job | Cloud Scheduler（每天 02:00 JST） | 把数据库 outbox 里的消息发到 Pub/Sub |
| `pianokt-pipeline` | Cloud Run Job | Cloud Scheduler（每天 03:00 JST） | 把对齐结果和行为事件灌进 Delta 湖 |
| Supabase 触发器 | PL/pgSQL | 表写入 | 行为事件自动进 outbox |
| Supabase RPC | PL/pgSQL | 前端调用 | 写事件、聚合成学习记录 |

## 1.2 端到端流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                         浏览器（Vercel）                          │
│                                                                  │
│  用户按空格 ──▶ player.play()                                     │
│       │                                                          │
│       ├──▶ useSegmentedRecordMidi 开始录制                        │
│       │     （按琴键写 noteOn/noteOff，每 100ms 推进静默时间轴）      │
│       │                                                          │
│       └──▶ emitPlayEvent('play_started')                         │
│                                                                  │
│  用户结束（自然弹完 / 主动退出）                                     │
│       │                                                          │
│       ├──▶ beginTerminal()  同步封存：停播放、导出 MIDI、释放 session │
│       │                                                          │
│       └──▶ finalizeSession()  异步：上传 + 写终止事件               │
└──────────────┬──────────────────────────────┬───────────────────┘
               │                              │
        路径 A：行为事件                  路径 B：MIDI 文件
               │                              │
               ▼                              ▼
   ┌───────────────────────┐    ┌──────────────────────────────┐
   │ Supabase RPC          │    │ POST /attempts               │
   │ log_play_event()      │    │  → pianokt-api               │
   │  ↓                    │    │  → 建 piano_attempts 行       │
   │ play_events_raw 表    │    │  → 返回 2 个 GCS 签名 URL      │
   │  ↓（数据库触发器）      │    └──────────┬───────────────────┘
   │ piano_outbox 表       │               │
   │                       │    浏览器直传 GCS（绕过后端）
   │ upsert_user_play_log()│               ▼
   │  ↓                    │    gs://…-raw/midi/{learner}/{attempt}/
   │ user_play_logs 表     │        ├── performance.mid
   │ （最终学习记录）        │        └── reference.mid
   └───────────────────────┘               │
                                POST /attempts/{id}/finalize
                                           │
                                  校验 SHA-256 + MIDI 头
                                           ▼
                                  piano_attempts.status = UPLOADED
                                  piano_outbox ← performance.uploaded
                                  challenge_recordings ← 一行
                                           │
        ┌──────────────────────────────────┘
        │  pianokt-relay（定时任务）
        ▼
   Pub/Sub topic: pianokt-events
        │
        ├─────────────────┬──────────────────────┐
        ▼                 ▼                      ▼
  订阅 alignment-worker  订阅 events-archive   （失败 5 次）
  filter=performance.    落盘 events/*.jsonl   pianokt-dead-letter
       uploaded               │
        │                     │
        ▼                     │
  pianokt-worker（私有）        │
   parangonar 对齐            │
        │                     │
        ├──▶ gs://…-raw/alignment/{run_id}/result.json
        ├──▶ gs://…-raw/matching/{run_id}/matches.json
        ├──▶ gs://…-raw/attempt-results/{attempt}/manifest.json
        ├──▶ piano_attempts.status = READY
        └──▶ piano_outbox ← alignment.completed
                              │
        ┌─────────────────────┘
        │  pianokt-pipeline（定时任务）
        ▼
   ┌────────────────────────┬─────────────────────────┐
   │ catch_up()             │ spark_events.run()      │
   │ 读 alignment/*.json    │ 读 events/*.jsonl       │
   │  ↓                     │  ↓                      │
   │ bronze/alignment_runs  │ bronze/practice_events  │
   │ bronze/score_notes     │ quality/practice_events │
   │ bronze/performance_…   │ silver/practice_events  │
   │  ↓ 质量门禁             │                         │
   │ silver/midi_alignment… │                         │
   │  ↓                     │                         │
   │ gold/fact_midi_align…  │                         │
   │ gold/hand_events       │                         │
   │ gold/fact_practice_…   │                         │
   │  ↓                     │                         │
   │ ops/completed_runs     │                         │
   └────────────────────────┴─────────────────────────┘
                  gs://…-lake/
```

## 1.3 为什么是两条路径

一个很自然的疑问：既然都要记录用户弹了什么，为什么不合成一条？

因为这两类数据的**用途、体积、时效性**完全不同。

行为事件（路径 A）回答的是「学习行为」问题：这个用户练了多久、有没有半途放弃、
注册第几天开始练的。它必须**立刻可用**——用户退出后马上就该在他的历史里看到这次练习。
数据量极小，每次练习几行文本。

MIDI 对齐（路径 B）回答的是「演奏质量」问题：第 37 个音符弹错了、右手整体偏慢 80 毫秒。
它需要跑一个几秒到几十秒的匹配算法，**不可能同步等待**。
数据量大，一次练习产生几千行音符级记录。

把它们强行合并，结果就是简单的东西被复杂的东西拖累。所以设计上让它们各走各的，
只在 `user_play_logs.challenge_recording_id` 这一个字段上建立关联。

## 1.4 时间尺度

理解整个系统的关键是搞清楚每一步要多久。

| 步骤 | 耗时 | 是否阻塞用户 |
|---|---|---|
| 录制 MIDI（内存中） | 0 | 否 |
| `beginTerminal()` 封存 | < 1ms | 否（同步但极快） |
| `POST /attempts` | ~200ms | 否（改造后后台执行） |
| 两次 GCS PUT | ~300ms | 否 |
| `POST /finalize` | ~500ms（含下载校验） | 否 |
| `log_play_event` RPC | ~150ms | 否 |
| `upsert_user_play_log` RPC | ~200ms | 否 |
| **relay 发消息** | **最长 24 小时**（定时任务） | 否 |
| **worker 对齐** | **几秒到几十秒** | 否 |
| **pipeline 入湖** | **最长 24 小时**（定时任务） | 否 |

注意后三行。当前 `schedules_paused = true`，两个定时任务是**暂停状态**，
意味着实际延迟是「无限大」——除非手动执行。详见第 12 章。

---

# 第 2 章 配置总清单

## 一句话版本

这个系统有 4 处需要填配置的地方：浏览器（Vercel）、Terraform（云资源）、
Cloud Run 的环境变量（由 Terraform 生成）、以及 Supabase 的数据库迁移。
填错一个的典型症状是「某个功能静默失效」，而不是明显报错，所以这一章逐项说明每个值的来源和后果。

## 2.1 前端环境变量

前端是 Vite 构建的。**Vite 只把以 `VITE_` 开头的变量注入到打包产物里**，而且是**构建时**注入，
不是运行时读取。这意味着：改了变量必须重新构建部署，不能只刷新页面。

配置位置：本地是仓库根目录的 `.env.local`；线上是 Vercel 项目设置里的 Environment Variables。

### `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY`

**消费者：** `src/features/auth/supabase.ts`

```ts
const url = import.meta.env.VITE_SUPABASE_URL as string | undefined
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined
export const supabase = url && anonKey ? createClient(url, anonKey) : null
```

**取值来源：** Supabase 控制台 → Project Settings → API。URL 形如
`https://nygtnkuisddcpbgitwhr.supabase.co`；anon key 可以是传统的 JWT 格式，
也可以是新的 `sb_publishable_…` 格式，两者都被网关接受。

**填错的后果：** 整个 `supabase` 对象变成 `null`。这不会抛异常，而是让所有依赖它的函数
走「未配置」分支。你会看到：

- `fetchLeaderboard()` 返回 `{ error: 'Supabase not configured' }`
- `logPlayEvent()` 返回同样的错误，所有行为事件静默丢失
- `backendRequest()` 抛 `'PianoKT backend is not configured'`——注意这句话具有误导性，
  它的判断条件是 `!apiRoot || !supabase`，所以 Supabase 没配也会报这句

**安全性：** anon key 是设计上公开的，放进前端包里没问题。它的权限完全由数据库的
Row Level Security 策略约束。**绝对不能**把 service-role key 或数据库密码放进 `VITE_*`。

### `VITE_PIANOKT_API_URL`

**消费者：** `src/features/challenge-history/gcs.ts`

```ts
const apiRoot = (import.meta.env.VITE_PIANOKT_API_URL as string | undefined)?.replace(/\/$/, '')
```

**取值来源：** `pianokt-api` 这个 Cloud Run Service 的地址，也就是 Terraform 的 `api_url` 输出：

```bash
terraform -chdir=infra/gcp output -raw api_url
# 或
gcloud run services describe pianokt-api --region us-central1 --format='value(status.url)'
```

**格式要求：** 只填根地址（`https://pianokt-api-xxxx.a.run.app`），不要带路径。
代码会自己拼 `/attempts`、`/attempts/{id}/finalize` 等。结尾的斜杠会被自动去掉，加不加都行。

**绝对不要填 worker 的地址。** `pianokt-worker` 是私有服务，只允许 Pub/Sub 的推送身份调用。
填成它的话浏览器会拿到 403，而 403 响应没有 CORS 头，浏览器会把它报成 `Failed to fetch`，
排查起来很费劲。

**填错的后果：** 挑战录音无法保存，用户看到 `Save failed: …`。行为事件不受影响（那条路径不经过后端）。

### `VITE_PUBLIC_GA_ID`

Google Analytics 的衡量 ID。可选，留空即禁用。

### 前端**不需要**的配置

这里明确一下，避免误配：

- **不需要** GCS 的任何凭据（HMAC key、service account JSON）。浏览器上传用的是后端签发的
  临时签名 URL，有效期 10 分钟，权限只够写那一个对象。
- **不需要** 数据库连接串。
- **不需要** Pub/Sub 的任何配置。

如果你在 Vercel 里看到了这些，应该立刻删掉并轮换对应的凭据。

## 2.2 Terraform 变量

配置位置：`infra/gcp/terraform.tfvars`。这个文件**不应该提交到仓库**。

`main.tf` 里声明的 9 个变量，其中 8 个是必填（无默认值），只有 `schedules_paused` 有默认值。
必填意味着 apply 时如果没提供，Terraform 会交互式提问——这也是为什么容易在早期部署时
随便填一个值蒙混过去，然后忘记它已经被烤进了云资源。

| 变量 | 示例值 | 影响的资源 |
|---|---|---|
| `project_id` | `pianokt` | 所有资源的项目；bucket 名前缀 |
| `region` | `us-central1` | 所有区域性资源 |
| `image` | `us-central1-docker.pkg.dev/pianokt/pianokt/backend:0.8.1` | api / worker / relay 三者的容器镜像 |
| `spark_image` | `…/pipeline:0.8.2` | 仅 pipeline job 的镜像（额外含 JVM 和 Spark） |
| `frontend_origin` | `https://pianokt-anu.vercel.app` | **API 的 CORS 白名单 + raw 桶的 CORS 规则** |
| `supabase_url` | `https://xxx.supabase.co` | api / worker 的 `SUPABASE_URL` |
| `supabase_anon_key` | `sb_publishable_…` | api / worker 的 `SUPABASE_ANON_KEY` |
| `database_secret_id` | `pianokt-database-url` | Secret Manager 里存数据库连接串的 secret 名 |
| `schedules_paused` | `true` | 两个 Cloud Scheduler 是否暂停 |

### `frontend_origin` 是最容易出事的一个

它同时喂给两个地方。第一处是 API 的 CORS：

```hcl
FRONTEND_ORIGINS = var.frontend_origin
```

第二处是 raw 桶的 CORS 规则：

```hcl
dynamic "cors" {
  for_each = each.key == "raw" ? [1] : []
  content {
    origin          = [var.frontend_origin]
    method          = ["PUT", "GET", "HEAD"]
    response_header = ["Content-Type", "ETag", "x-goog-generation", "x-goog-if-generation-match"]
    max_age_seconds = 3600
  }
}
```

也就是说，**同一个值决定了两次跨域请求能不能通过**：浏览器打 API、以及浏览器直传 GCS。
任何一处不匹配，症状都是 `Failed to fetch`，而且浏览器出于安全原因不会告诉你具体细节。

**已知限制：** 这个变量是单个字符串，所以只能允许一个来源。Vercel 的预览部署域名
（`xxx-git-branch-yyy.vercel.app`）和 `localhost:5173` 都会被挡在外面。
API 那边的 `os.getenv('FRONTEND_ORIGINS').split(',')` 其实支持逗号分隔的多值，
但 bucket 那边写的是 `origin = [var.frontend_origin]`，传逗号串会变成一个含逗号的非法 origin。
要支持多域名，得把这个变量改成 `list(string)` 并同步改两处引用。

### `database_secret_id` 与密钥管理

注意这里传的是 **secret 的名字**，不是密码本身。Terraform 只引用它：

```hcl
env {
  name = "DATABASE_URL"
  value_source {
    secret_key_ref {
      secret  = var.database_secret_id
      version = "latest"
    }
  }
}
```

这样设计是为了让**密码内容永远不进入 Terraform state**。state 文件存在 GCS 后端里，
虽然有访问控制，但它是明文的——任何能读 state 的人都能看到里面所有变量的值。
所以 secret 要用 `gcloud secrets create` 手工创建，Terraform 只负责授权谁能读。

连接串的格式参考 `backend/.env.example`：

```
postgresql://pianokt_backend:PASSWORD@SUPABASE_SESSION_POOLER:5432/postgres?sslmode=require
```

用的是专用角色 `pianokt_backend`（见第 11 章），不是 postgres 超级用户。

### `schedules_paused`

默认 `true`，当前 tfvars 里也是 `true`。它控制两个 Cloud Scheduler：

```hcl
resource "google_cloud_scheduler_job" "job" {
  for_each  = google_cloud_run_v2_job.job
  name      = "pianokt-${each.key}"
  schedule  = each.key == "relay" ? "0 2 * * *" : "0 3 * * *"
  time_zone = "Asia/Tokyo"
  paused    = var.schedules_paused
```

`true` 时整条离线链路完全静止：outbox 不会被发布、Pub/Sub 收不到消息、
worker 不会被触发、Delta 湖一张表都不会生成。开发阶段这样省钱，
但如果你以为系统在跑而实际上没跑，会浪费大量排查时间。

## 2.3 Cloud Run 的环境变量

这里有一个**容易忽略的关键点：Service 和 Job 拿到的是两套完全不同的环境变量。**

### Service（api / worker）

```hcl
dynamic "env" {
  for_each = {
    RAW_ROOT                = "gs://${google_storage_bucket.data["raw"].name}"
    SUPABASE_URL            = var.supabase_url
    SUPABASE_ANON_KEY       = var.supabase_anon_key
    FRONTEND_ORIGINS        = var.frontend_origin
    SIGNING_SERVICE_ACCOUNT = google_service_account.runtime["api"].email
  }
  ...
}
```

加上单独声明的 `DATABASE_URL`（来自 Secret Manager），一共 6 个。

| 变量 | 谁在用 | 用途 |
|---|---|---|
| `DATABASE_URL` | api, worker | 连 Postgres |
| `RAW_ROOT` | api, worker | `Objects(os.environ['RAW_ROOT'])`，即 raw 桶根路径 |
| `SUPABASE_URL` | api | 校验 Bearer token、调 `check_user_allowed` |
| `SUPABASE_ANON_KEY` | api | 上述两个调用的 `apikey` 头 |
| `FRONTEND_ORIGINS` | api | CORS `allow_origins`，逗号分隔 |
| `SIGNING_SERVICE_ACCOUNT` | api | 生成 V4 签名 URL 时的模拟身份 |

注意 worker 拿到了 `SUPABASE_URL` 等变量但用不上——它不做 HTTP 鉴权，
只被 Pub/Sub 用 IAM 调用。这是 Terraform 里两个服务共用一个 `for_each` 的副作用，无害。

### Job（relay / pipeline）

```hcl
dynamic "env" {
  for_each = {
    RAW_ROOT            = "gs://…-raw"
    LAKE_ROOT           = "gs://…-lake"
    CHECKPOINT_ROOT     = "gs://…-checkpoint"
    EVENT_TOPIC         = google_pubsub_topic.events.id
    ENABLE_SPARK_EVENTS = "1"
  }
  ...
}
```

同样加上 `DATABASE_URL`，一共 6 个，但**内容和 Service 完全不同**。

| 变量 | 谁在用 | 用途 |
|---|---|---|
| `DATABASE_URL` | relay, pipeline | relay 读 outbox；pipeline 抢咨询锁 |
| `RAW_ROOT` | pipeline | 读 `alignment/*/result.json` 和 `events/*.jsonl` |
| `LAKE_ROOT` | pipeline | Delta 表根目录 |
| `CHECKPOINT_ROOT` | pipeline | Spark 流式作业断点 |
| `EVENT_TOPIC` | relay | `publisher.publish(os.environ['EVENT_TOPIC'], …)` |
| `ENABLE_SPARK_EVENTS` | pipeline | `=='1'` 时才跑 Spark 那条支线 |

**Service 没有 `EVENT_TOPIC`**，这是刻意的：api 和 worker 从不直接发消息，
它们只往 `piano_outbox` 表写行。发消息是 relay 的专职工作（第 6 章解释为什么）。

**Job 没有 `SUPABASE_URL` / `FRONTEND_ORIGINS` / `SIGNING_SERVICE_ACCOUNT`**，
因为离线任务不处理 HTTP 请求，不需要鉴权也不需要签 URL。

### 本地开发

`backend/.env.example` 列了完整的 10 项，是上面两套的并集加上注释。
本地跑的时候 `RAW_ROOT` 可以填本地路径（如 `data/raw`），`Objects` 类会自动切换到文件系统模式：

```python
def __init__(self, root):
    self.root = root.rstrip('/'); self.bucket = None
    if root.startswith('gs://'):
        from google.cloud import storage
        p = urlparse(root); self.prefix = p.path.strip('/')
        self.bucket = storage.Client().bucket(p.netloc)
    else: self.path = Path(root).resolve()
```

## 2.4 Supabase 迁移清单

配置的第四处是数据库结构。这些 SQL 文件**不会自动应用**，需要手工在
Supabase Dashboard 的 SQL Editor 里逐个执行。漏跑一个的典型症状是某个 RPC 返回 404，
然后被上层包装成一个看不出所以然的错误。

| 文件 | 创建的对象 | 漏跑的症状 |
|---|---|---|
| `000_run_this_first_save_challenge_recording.sql` | 早期版本的保存 RPC | 被 001 覆盖，可跳过 |
| `001_challenge_recordings_and_rpc.sql` | `challenge_recordings` 表、`save_challenge_recording`、`get_leaderboard`、Storage bucket 及策略 | 排行榜和录音列表全空 |
| `002_auth_whitelist.sql` | `allowed_emails` 表、`check_user_allowed` RPC | **后端所有 API 返回 503 `Access policy unavailable`** |
| `003_profiles_sync_from_auth.sql` | `profiles` 表 + 两个 auth 触发器 | 排行榜没有昵称和头像 |
| `004_upsert_profile_from_auth_rpc.sql` | `upsert_profile_from_auth` RPC | 前端静默忽略（`.then(()=>{}, ()=>{})`） |
| `005_challenge_recordings_midi_keyboard_used.sql` | 加 `midi_keyboard_used` 列 | finalize 插入失败 |
| `006_play_events_pipeline.sql` | `play_events_raw`、`user_play_logs`、`log_play_event`、`upsert_user_play_log` | 行为事件完全无法记录 |
| `007_user_play_logs_song_time_and_recording_link.sql` | 加 `song_time_sec`、`challenge_recording_id` + 外键；替换聚合函数 | 学习记录里查不到对应录音 |
| `008_drop_legacy_columns_from_user_play_logs.sql` | 删除 7 个冗余列 | 表里留着无用列，无功能影响 |
| `009_lakehouse_backend.sql` | 7 张 `piano_*` 表 + `piano_emit_play_event` 触发器 | 后端 `POST /attempts` 直接 500 |
| `backend_role.sql` | `pianokt_backend` 数据库角色及授权 | 后端连不上或权限不足 |

### 002 缺失是一个典型的连锁故障

值得单独讲，因为它演示了「静默失效」是怎么发生的。

后端每个请求都会调这个 RPC：

```python
allowed = httpx.post(
    os.environ['SUPABASE_URL'].rstrip('/') + '/rest/v1/rpc/check_user_allowed',
    headers={'Authorization': authorization, 'apikey': os.environ['SUPABASE_ANON_KEY']},
    json={}, timeout=10)
if allowed.status_code != 200: raise HTTPException(503, 'Access policy unavailable')
```

函数不存在时 PostgREST 返回 404（错误码 `PGRST202`），于是后端报 503。
错误信息说的是「访问策略不可用」，听起来像是网络问题或权限配置问题，
完全看不出真正的原因是「一个 SQL 文件没跑」。

而前端调同一个 RPC 时是这样写的：

```ts
const { data, error: rpcErr } = await supabase.rpc('check_user_allowed')
if (rpcErr) return
```

`if (rpcErr) return` 是 **fail-open**——RPC 挂了就当检查通过。所以前端从来不会暴露这个问题，
白名单机制可以长期处于「完全没生效」的状态而无人察觉。后端是 fail-closed，
它才是把问题捅出来的那一方。

诊断命令（不需要登录）：

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST 'https://YOUR_PROJECT.supabase.co/rest/v1/rpc/check_user_allowed' \
  -H 'apikey: YOUR_ANON_KEY' -H 'Content-Type: application/json' -d '{}'
```

返回 404 就是没跑；返回 200 说明函数存在。

---

# 第 3 章 数据是怎么产生的：浏览器端

## 一句话版本

用户看到的是「弹琴」，系统看到的是三样东西同时在生成：一份记录他按了哪些键的 MIDI 文件、
一份记录他本来应该弹什么的标准答案 MIDI、以及一串「他什么时候开始、暂停、退出」的操作日志。
这一章讲这三样东西各自是怎么被造出来的。

## 3.1 演奏 MIDI：为什么不能只记录按键

最直觉的录制方式是「用户按一个键就记一条」。这样做有个致命问题：
**沉默的时间会消失**。如果用户开头愣了 5 秒才弹第一个音，
录出来的 MIDI 里第一个音仍然在 0 秒位置，后续所有对齐都会错位。

`src/features/midi/useSegmentedRecordMidi.ts` 的做法是**用歌曲时间驱动时间轴**，
而不是用按键事件驱动。核心是一个 `advanceTo()`：

```ts
const advanceTo = (songTimeSec: number) => {
  if (!recorderRef.current) return
  const last = lastSongTimeSecRef.current
  if (last == null) { lastSongTimeSecRef.current = songTimeSec; return }
  const deltaSec = songTimeSec - last
  if (deltaSec <= 0) return
  const deltaTicks = secToTicks(deltaSec, DEFAULT_BPM)
  if (deltaTicks > 0) recorderRef.current.addSilenceTicks(deltaTicks)
  lastSongTimeSecRef.current = songTimeSec
}
```

`addSilenceTicks` 写入的是一个 MIDI Marker 元事件（`0xFF 0x06`，文本内容是一个点），
它不发声，唯一作用是把时间轴往前推。挑战页面每 100 毫秒调用一次 `flushSilenceTo()`：

```ts
const timer = window.setInterval(() => {
  const t = nowSongSec()
  const dur = (player as any).getDuration?.() ?? 0
  lastSongTimeRef.current = t
  if (dur > 0) lastDurationRef.current = dur
  flushSilenceTo(t)
}, 100)
```

这样即使用户一个键都不按，MIDI 也在变长。等到真有按键事件时，
先 `advanceTo(当前歌曲时间)` 把时间推到位，再写 `noteOn`，delta 用 0：

```ts
if (eventClockRef.current) advanceTo(eventClockRef.current())
if (e.type === 'down') recorderRef.current.noteOn(0, note, vel)
else                   recorderRef.current.noteOff(0, note, vel)
```

**时间基准的含义：** 用的是「歌曲时间」（song time），不是墙上时钟。
两者在正常播放时同步，但在两种情况下会分叉：变速播放（`bpmModifier`）
和等待模式（`waiting`，播放器会停下来等用户弹对才继续）。
后者会让时间标签失去意义，所以练习设置里记了 `time_basis: 'song_time'`，
对齐结果里也会带上 `timing_labels_valid`，训练数据导出时直接排除等待模式的样本。

**导出格式：** format 0（单轨）、PPQ 480、固定 120 BPM。
120 BPM 只是把「秒」编码成「tick」的一种换算方式，不代表用户必须以 120 BPM 弹：

```ts
function secToTicks(sec: number, bpm = DEFAULT_BPM) {
  return Math.round(sec * (bpm / 60) * PPQ)
}
```

`stopRecording(songTimeSec, targetDurationSec?)` 会先补齐静默到当前时刻，
如果传了原曲总时长还会再补到那个长度，保证导出的 MIDI 和原曲等长。
然后调 `finish()` 写 End Of Track 并把 recorder 置空。

## 3.2 参考 MIDI：标准答案也是动态生成的

对齐需要一份「本来应该弹成什么样」的参考。但这份参考**不能**是整首曲子的原始乐谱，
因为用户可能只练右手、或者只练中间 8 小节、或者移调了。
拿全曲双手谱去比对只练右手的演奏，会得到一大堆虚假的「漏音」。

`src/features/challenge-history/referenceSnapshot.ts` 每次终止时现场生成一份快照：

```ts
export function referenceSnapshot(song: Song, config: SongConfig, range?: {start:number; end:number}): Uint8Array {
  const midi = new Midi(); midi.header.setTempo(120)
  const start = range?.start ?? 0, end = range?.end ?? song.duration
  const hands = new Map<string, ReturnType<Midi['addTrack']>>()
  for (const note of song.notes) {
    const hand = config.tracks[note.track]?.hand
    if (hand === 'none' || (hand === 'left' && !config.left) || (hand === 'right' && !config.right)) continue
    if (note.time < start || note.time >= end) continue
    const label = hand === 'left' ? 'left' : hand === 'right' ? 'right' : `track-${note.track}`
    let track = hands.get(label)
    if (!track) { track = midi.addTrack(); track.name = label; hands.set(label, track) }
    const pitch = note.midiNote + (config.transpose ?? 0)
    if (pitch < 0 || pitch > 127) throw new Error('Transposed pitch outside MIDI range')
    track.addNote({ midi: pitch, time: note.time-start, duration: Math.min(note.duration,end-note.time), velocity: note.velocity ?? 0.8 })
  }
  return midi.toArray()
}
```

逐行看它做了什么筛选：

1. **按手过滤。** `config.tracks[note.track].hand` 是这个音轨被判定为左手还是右手。
   如果用户关了左手（`config.left === false`），所有左手音符被跳过。
2. **按片段裁剪。** 只保留 `[start, end)` 区间内的音符，且时间被平移到从 0 开始
   （`note.time - start`）。所以参考谱的 0 秒对应用户选择片段的起点。
3. **按移调调整。** 每个音高加上 `config.transpose`，超出 MIDI 范围直接抛错。
4. **保留手部命名。** 音轨名字是字面的 `'left'` / `'right'`，
   后端的手部推断逻辑依赖这个命名。

**一个已知的信任边界：** 这份参考谱来自登录用户的浏览器。SHA-256 能证明后续处理的
是同一份字节，但**不能**证明这份乐谱是平台认证的权威版本。用于正式考试或防作弊之前，
需要在服务端建立曲谱版本目录并做校验。这一点在教学文档里也明确标注了。

## 3.3 行为事件：session 的生命周期

行为事件用「session」这个概念把一次连续的练习串起来。session ID 是一个客户端生成的 UUID，
存在 `playSessionIdRef` 里。

### 状态转换

```
        ┌──────────────┐
        │   无 session  │ ◀──────────────────┐
        └──────┬───────┘                     │
               │ 用户按 Start / 空格           │
               │ playSessionIdRef = uuid()    │
               ▼                              │
        ┌──────────────┐                      │
   ┌───▶│   演奏中      │                      │
   │    └──────┬───────┘                      │
   │           │                              │
   │  paused   │  finished / exited           │
   │           ▼                              │
   │    ┌──────────────┐                      │
   └────│   已暂停      │                      │
        └──────┬───────┘                      │
               │ 终止                          │
               ▼                              │
        ┌──────────────┐                      │
        │  finalizing   │──────────────────────┘
        │ (上传中，禁止恢复)│
        └──────────────┘
```

对应到代码，`src/pages/challenge/page.tsx` 里有 4 个 ref 维护这个状态机：

| ref | 类型 | 含义 |
|---|---|---|
| `playSessionIdRef` | `string \| null` | 当前 session ID，null 表示未开始 |
| `playStartedAtMsRef` | `number \| null` | 本段演奏的起始墙钟时间 |
| `accumulatedPlayMsRef` | `number` | 之前各段累计的活跃时长 |
| `terminalFlowInFlightRef` | `boolean` | 是否正在执行终止流程 |

注意 `time_playing`（活跃演奏时长）和 `song_time_sec`（播放头位置）是两个不同的量。
用户在 30 秒处暂停 10 分钟再继续，前者只增加了实际播放的时间，后者仍然是 30 秒。

```ts
const getTimePlayingSec = () => {
  let elapsedMs = accumulatedPlayMsRef.current
  if (playStartedAtMsRef.current != null) {
    elapsedMs += performance.now() - playStartedAtMsRef.current
  }
  return Math.max(0, elapsedMs / 1000)
}
```

### 六种事件类型

数据库的 CHECK 约束限定了取值：

```sql
check (event_type in ('play_started', 'paused', 'resumed', 'finished', 'exited', 'failed'))
```

其中 `finished` / `exited` / `failed` 是**终止事件**。前端有一个对应的集合：

```ts
const TERMINAL_PLAY_EVENT_TYPES = new Set<PlayEventType>(['finished', 'exited', 'failed'])
```

只有终止事件写入成功后，`logPlayEvent()` 才会继续调 `upsert_user_play_log()` 做聚合。
普通事件写完直接返回。

### 终止流程为什么必须是同步的

这里有一个设计上的关键点，值得详细说明，因为它是一个真实踩过的坑。

终止流程要做的事情包括：停播放、封存录音、上传到云端、写终止事件。
其中上传是网络操作，要几百毫秒到几秒。如果整个流程写成一个 `async` 函数从头 await 到尾，
就会出现一个**危险的时间窗口**：在上传进行中，`playSessionIdRef` 还没被清空，
而空格键监听器、播放按钮都还是活的。用户这时按一下空格，代码会认为
「session 还在，这是一次 resume」，于是往一个**已经终止的 session** 里写 `resumed` 事件，
并重新开始播放。

更糟的是顺序会颠倒：`exited` 是 await 完上传才写库的，而窗口里的 `resumed` 立刻就写了。
数据库里的事件序列变成 `… → resumed → exited`，聚合函数算出来的
`ended_at`、`events_count`、`time_playing` 全部失真。

解决办法是把终止动作压缩成一个**在任何 await 之前完成的同步块**：

```ts
const beginTerminal = (
  songTimeSec: number,
  { stopPlayback }: { stopPlayback: boolean },
): TerminalContext | null => {
  if (terminalFlowInFlightRef.current) return null
  const sessionId = playSessionIdRef.current
  if (!sessionId) return null

  // player.stop() 会重置歌曲时间、统计数据和选定片段，所以必须先读出来
  const durationSec = lastDurationRef.current || ((player as any).getDuration?.() ?? 0)
  const accuracyPct = (player as any).store?.get?.((player as any).score?.accuracy) ?? 0
  const accuracy = typeof accuracyPct === 'number' ? accuracyPct : 0
  const range = selectedRange
  const referenceMidiBase64 = song ? bytesToBase64(referenceSnapshot(song, songConfig, range)) : ''

  setTerminalFlowInFlight(true)
  playSessionIdRef.current = null        // ← 关键：同步释放，后续 handler 拿不到旧 session
  markPlayingStopped()
  const midiBytes = stopRecording(songTimeSec, durationSec > 0 ? durationSec : undefined)
  if (stopPlayback) {
    pausedByUserRef.current = true
    player.stop()
  }

  return { sessionId, midiBytes, referenceMidiBase64, range, durationSec, accuracy, songTimeSec }
}
```

`playSessionIdRef.current = null` 这一行是核心。清空之后，如果用户再按空格，
`isNewSession` 判定为 `true`，会开一个全新的 session 发 `play_started`——
这正是业务上想要的语义：**退出之后必须重新开始挑战，不能续上**。

`stopPlayback` 参数为什么存在：自然弹完时播放器已经在 `playLoop_` 里 `pause()` 过了，
这时再调 `player.stop()` 会触发 `reset_()`，把歌曲时间归零、清空统计、
还会**清掉用户选的练习片段**。而成功弹窗是可关闭的，用户关掉后就会看到一个被重置的页面。
所以只有主动退出那条路径传 `true`。

同理 `pausedByUserRef` 也绑定在 `stopPlayback` 上。这个 ref 是**一次性消费**的：

```ts
if (pausedByUserRef.current) {
  pausedByUserRef.current = false
  previousPlayingRef.current = isPlayingNow
  return
}
```

如果在自然结束路径也置位，它不会被当次消费掉，会残留到下一局，
导致**下一局的自然结束被静默吞掉**。

### 三处恢复入口的守卫

`terminalFlowInFlightRef` 只写不读是没有意义的。三个能恢复播放的地方都要检查它：

```ts
// 1. 播放按钮
const handleTogglePlayingChallenge = () => {
  if (terminalFlowInFlightRef.current) { showToast('Saving your recording…'); return }
  ...
}

// 2. 空格键（监听器挂在 window 上，弹窗遮罩拦不住）
useEventListener<KeyboardEvent>('keydown', (evt) => {
  if (evt.code !== 'Space') return
  evt.preventDefault()
  if (terminalFlowInFlightRef.current || showSuccessModal) return
  ...
})

// 3. 确认退出弹窗的 Continue
onClick={() => {
  if (terminalFlowInFlightRef.current) return
  ...
}}
```

空格键那处特别值得注意。`useEventListener` 的默认目标是 `globalThis`：

```ts
element: Element | typeof globalThis = globalThis,
```

而成功弹窗用的是 react-aria 的 `ModalOverlay`，它做的是焦点陷阱和鼠标遮挡，
**keydown 事件仍然会冒泡到 window**。所以弹窗开着的时候空格键依然会触发，
必须显式判断 `showSuccessModal`。

## 3.4 前端 API 层的三个函数

`src/features/challenge-history/api.ts` 对外暴露的关键函数：

### `logPlayEvent(params)`

写一条事件，终止事件额外触发聚合。注意它**不接受 userId 参数**：

```ts
const { data: { user } } = await supabase.auth.getUser()
if (!user) return { error: 'Not authenticated' }

const { error: eventError } = await supabase.rpc('log_play_event', {
  p_event_id: crypto.randomUUID(),
  p_session_id: params.sessionId,
  p_song_id: params.songId,
  ...
})
```

真正的 `user_id` 由数据库函数内部的 `auth.uid()` 决定。
客户端就算伪造也没用，这是防止越权写入的基本手段。

`p_event_id` 每次调用都是新的 UUID，而 `p_session_id` 在同一次演奏中保持不变。
数据库那边有 `on conflict (event_id) do nothing`，所以重复提交同一个 event_id 是幂等的。

### `finalizeUserPlayLog(sessionId)`

单独调用聚合。正常情况下由 `logPlayEvent` 自动触发，导出它是为了手动补偿：

```ts
const finalizeResult = await finalizeUserPlayLog(params.sessionId)
if ('error' in finalizeResult) {
  console.error('[logPlayEvent] Raw terminal event was saved, but user play log aggregation failed:', finalizeResult.error)
  return { error: 'Play event saved, but aggregation failed: ' + finalizeResult.error }
}
```

这里有一个**明确承认的非原子性**：`log_play_event` 和 `upsert_user_play_log`
是两次独立的 RPC，顺序靠 `await` 保证，但它们不在同一个 PostgreSQL 事务里。
可能出现「终止事件已存但聚合失败」的中间态。原始数据不会丢，
事后重新调 `finalizeUserPlayLog` 就能修复汇总。

### `saveChallengeRecording` → 实际是 `saveGcsRecording`

```ts
export { saveGcsRecording as saveChallengeRecording } from './gcs'
```

这是一个历史遗留的别名。早期版本录音存 Supabase Storage，现在改成了 GCS，
但调用方的名字没变。下载时靠路径前缀区分新旧：

```ts
export async function getChallengeRecordingDownloadUrl(storagePath: string): Promise<string | null> {
  if (storagePath.startsWith('gcs:')) {
    return (await backendRequest<{ url: string }>(`/attempts/${storagePath.slice(4)}/download`)).url
  }
  if (!supabase) return null
  const { data } = await supabase.storage.from(BUCKET).createSignedUrl(storagePath, 3600)
  return data?.signedUrl ?? null
}
```

---

# 第 4 章 两条并行的写入链路

## 一句话版本

一次练习会在数据库里留下两组痕迹：一组记录「行为」（什么时候开始、练了多久、有没有弹完），
另一组记录「作品」（这段演奏的 MIDI 文件存在哪、对齐算得怎么样）。
它们各有各的表，只在一个字段上互相指认。

## 4.1 表的全貌

`public` schema 下和这套流程相关的表，按所属链路分组：

**链路 A：行为事件（migration 006 / 007 / 008）**

- `play_events_raw` — 原始事件，只增不改
- `user_play_logs` — 按 session 聚合的学习记录

**链路 B：演奏作品（migration 001 / 005 / 009）**

- `piano_attempts` — 一次上传任务的完整状态机
- `piano_learner_keys` — 用户 ID ↔ 伪名 ID 的映射
- `challenge_recordings` — 面向前端展示的录音列表
- `piano_outbox` — 待发布的消息队列（两条链路共用）

**辅助表**

- `profiles` — 昵称头像，用于排行榜
- `allowed_emails` — 邮箱白名单
- `piano_recommendations` / `piano_recommendation_feedback` — 推荐与埋点
- `piano_song_catalog` / `piano_preferences` — 曲库与偏好

## 4.2 `play_events_raw`：只增不改的事实表

```sql
create table if not exists public.play_events_raw (
  event_id uuid primary key,
  session_id uuid not null,
  user_id uuid not null references auth.users(id) on delete cascade,
  song_id text not null,
  exercise_id text,
  play_mode text not null,
  event_type text not null,
  song_time_sec numeric,
  client_ts timestamptz,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  check (play_mode in ('challenge', 'freeplay', 'training')),
  check (event_type in ('play_started', 'paused', 'resumed', 'finished', 'exited', 'failed'))
);
```

字段逐个说明：

| 字段 | 含义 | 注意事项 |
|---|---|---|
| `event_id` | 每条事件唯一 | 客户端生成，用于幂等 |
| `session_id` | 一次连续练习 | 同一次演奏的所有事件共享 |
| `user_id` | 归属用户 | 由 `auth.uid()` 填充，客户端不可控 |
| `exercise_id` | 练习标识 | 挑战模式下形如 `challenge:{songId}` |
| `song_time_sec` | 播放头位置 | **不是**活跃时长 |
| `client_ts` | 客户端时间 | 可能与服务端时间有偏差，仅供参考 |
| `created_at` | 服务端时间 | 排序和聚合都以这个为准 |
| `metadata` | 任意附加数据 | 关键信息都塞在这里，见下 |

两个索引服务于两种查询：

```sql
create index play_events_raw_user_created_idx  on public.play_events_raw (user_id, created_at desc);
create index play_events_raw_session_created_idx on public.play_events_raw (session_id, created_at asc);
```

前者用于「查某用户最近的事件」，后者用于「按时间顺序重放某个 session」——
聚合函数走的就是第二个。

RLS 只开了 select 和 insert，都限定 `auth.uid() = user_id`。
**没有 update 和 delete 策略**，意味着这张表对普通用户是只增不改的。

### `metadata` 里都有什么

前端每条事件都会带上一组固定字段：

```ts
metadata: {
  source,
  song_duration_sec: getSongDurationSec(),
  time_playing_sec: Number(getTimePlayingSec().toFixed(3)),
  difficulty: songMeta?.difficulty ?? null,
  ...metadata,     // ← 调用方的额外字段覆盖上面的默认值
}
```

终止事件会额外带：

```ts
{
  reason,                                    // exited 才有：back_button / confirm_exit
  success,                                   // finished 才有：是否达标
  accuracy_pct: terminal.accuracy,
  challenge_recording_id: recordingId,       // 关联到 challenge_recordings
  song_time_sec: terminal.songTimeSec,
  song_duration_sec: terminal.durationSec,   // 用快照值覆盖，防止页面卸载后读到 0
}
```

最后一项值得解释。`getSongDurationSec()` 读的是活的 player 对象，
但退出流程是在导航之后异步执行的，那时页面已经卸载。所以终止事件用
`beginTerminal` 捕获的快照值覆盖默认值——因为 `...metadata` 在展开顺序上排在后面。

## 4.3 `user_play_logs`：聚合出来的学习记录

```sql
create table if not exists public.user_play_logs (
  session_id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  song_id text not null,
  exercise_id text,
  play_mode text not null,
  days_since_signup numeric,
  time_playing numeric not null default 0,
  is_played_in_full boolean not null default false,
  exit_status text not null,
  started_at timestamptz not null,
  ended_at timestamptz not null,
  events_count integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (exit_status in ('succeeded', 'abandoned', 'failed', 'unknown'))
);
```

migration 007 追加了两列：

```sql
alter table public.user_play_logs add column if not exists song_time_sec numeric;
alter table public.user_play_logs add column if not exists challenge_recording_id uuid;
-- 外键 → challenge_recordings(id) on delete set null
```

migration 008 删掉了 7 个冗余列（`song_source`、`song_title`、`duration_sec`、
`midi_storage_path`、`accuracy_pct`、`difficulty`，以及一个拼错的 `song_tittle`）。
这些信息现在统一从 `challenge_recordings` 那边取，避免同一份数据存两处然后不一致。

RLS 只有 select 策略。**没有 insert 策略**，因为这张表只能由
`security definer` 的聚合函数写入，用户不能直接插。

## 4.4 聚合函数：`upsert_user_play_log`

这是整条行为链路的核心逻辑。它把一个 session 的所有原始事件压缩成一行。

### 第一步：解析身份

```sql
v_user_id := auth.uid();
if v_user_id is null then
  return;
end if;
```

这两行看着平淡，但它解决了一个实际踩过的 SQL 错误。早期版本直接在聚合 SELECT 里
写 `e.user_id`，PostgreSQL 报 `42803: column "e.user_id" must appear in the GROUP BY clause`。
因为一个查询里既有聚合函数（`min`、`max`、`count`）又有裸列时，
裸列必须出现在 GROUP BY 里。把 `auth.uid()` 先存进变量，
然后用它做 WHERE 过滤而不是 SELECT 输出，问题就消失了。

### 第二步：一次扫描算出所有聚合量

```sql
select
  min(e.song_id), min(e.exercise_id), min(e.play_mode),
  min(e.created_at), max(e.created_at), count(*)::integer,
  max(e.song_time_sec),
  max(case when jsonb_typeof(e.metadata -> 'song_duration_sec') = 'number'
           then (e.metadata ->> 'song_duration_sec')::numeric else null end)
into
  v_song_id, v_exercise_id, v_play_mode,
  v_started_at, v_ended_at, v_events_count,
  v_progress_max, v_duration_target
from public.play_events_raw e
where e.session_id = p_session_id and e.user_id = v_user_id;

if coalesce(v_events_count, 0) = 0 then
  return;
end if;
```

`min(song_id)` 这种写法看起来奇怪——同一个 session 的 song_id 本来就该相同，
用 `min` 只是为了满足聚合查询的语法要求，随便取一个即可。

`jsonb_typeof(...) = 'number'` 这个判断是必要的防御。如果前端不小心把
`song_duration_sec` 传成了字符串 `"123"`，直接 `::numeric` 转换在某些情况下会成功，
在另一些情况下会抛异常。显式检查类型可以让脏数据变成 NULL 而不是让整个函数崩掉。

### 第三步：找出终止事件

```sql
select e.event_type, e.metadata
into v_terminal_event, v_terminal_metadata
from public.play_events_raw e
where e.session_id = p_session_id and e.user_id = v_user_id
  and e.event_type in ('finished', 'exited', 'failed')
order by e.created_at desc
limit 1;
```

取**最后一个**终止事件。正常情况下一个 session 只有一个，
但如果因为重试产生了多个，以最新的为准。

如果一个 session 从来没有终止事件（用户直接关了浏览器），
`v_terminal_event` 是 NULL，后面 `exit_status` 会落到 `'unknown'` 分支。
这也解释了一个常见困惑：**为什么 `play_events_raw` 里有数据但 `user_play_logs` 是空的**——
因为聚合只在终止事件到达时才被触发。

### 第四步：计算派生指标

```sql
v_time_playing := coalesce(
  case when jsonb_typeof(v_terminal_metadata -> 'time_playing_sec') = 'number'
       then (v_terminal_metadata ->> 'time_playing_sec')::numeric else null end,
  greatest(coalesce(v_progress_max, 0), 0)
);
```

优先用终止事件里客户端上报的活跃时长；没有的话退化成「播放头到过的最远位置」。
后者是一个更粗糙的近似（它把暂停时间也算进去了），但总比 0 强。

```sql
v_is_played_in_full := coalesce((v_terminal_event = 'finished'), false)
  or (coalesce(v_duration_target, 0) > 0
      and coalesce(v_progress_max, 0) >= v_duration_target * 0.98);
```

两个判定条件取或：要么终止事件是 `finished`（播放器自己判定播完了），
要么播放头到过总时长的 98% 以上。留 2% 余量是因为浮点误差和最后一个音符的尾巴。

```sql
if v_terminal_event = 'finished' then
  v_exit_status := case when coalesce(v_terminal_metadata ->> 'success', 'false') = 'true'
                        then 'succeeded' else 'failed' end;
elsif v_terminal_event = 'exited' then v_exit_status := 'abandoned';
elsif v_terminal_event = 'failed' then v_exit_status := 'failed';
else v_exit_status := 'unknown';
end if;
```

注意 `finished` 会分裂成 `succeeded` 和 `failed` 两种结果，
取决于 metadata 里的 `success` 标志（前端用 `isChallengeSuccess(accuracy)` 算的，
阈值是 90%）。所以「弹完了」和「弹好了」在数据里是分开的。

```sql
select u.created_at into v_user_created_at from auth.users u where u.id = v_user_id;
if v_user_created_at is not null then
  v_days_since_signup := extract(epoch from (v_started_at - v_user_created_at)) / 86400.0;
end if;
```

`days_since_signup` 是浮点天数。注册后 9 分钟开始练习会得到 `0.00625`。
这个指标用于分析用户留存曲线。

### 第五步：找出对应的录音

```sql
begin
  v_challenge_recording_id := nullif(v_terminal_metadata ->> 'challenge_recording_id', '')::uuid;
exception
  when invalid_text_representation then v_challenge_recording_id := null;
end;

-- 历史数据兜底：metadata 里没带 recording ID 时，按时间就近匹配
if v_challenge_recording_id is null and v_play_mode = 'challenge' then
  select cr.id into v_challenge_recording_id
  from public.challenge_recordings cr
  where cr.user_id = v_user_id and cr.song_id = v_song_id
    and cr.created_at between v_started_at - interval '30 minutes'
                         and v_ended_at + interval '10 minutes'
  order by abs(extract(epoch from (cr.created_at - v_ended_at))) asc
  limit 1;
end if;
```

主路径是直接从终止事件的 metadata 里读。`exception when invalid_text_representation`
是防止一个格式错误的字符串把整个函数搞崩。

兜底路径按「同用户 + 同歌曲 + 时间窗口内 + 距离结束时刻最近」匹配。
这个逻辑只在主路径失败时生效，主要服务于两种情况：
历史数据（当时前端还没传这个字段），以及上传失败但录音其实存下来了的边缘情况。

时间窗口取 `[开始前 30 分钟, 结束后 10 分钟]`。前面留得宽是因为
录音的 `created_at` 是 finalize 时刻，可能早于聚合时刻。

### 第六步：写入

```sql
insert into public.user_play_logs (...) values (...)
on conflict (session_id) do update set
  days_since_signup = excluded.days_since_signup,
  time_playing = excluded.time_playing,
  song_time_sec = excluded.song_time_sec,
  challenge_recording_id = excluded.challenge_recording_id,
  is_played_in_full = excluded.is_played_in_full,
  exit_status = excluded.exit_status,
  ended_at = excluded.ended_at,
  events_count = excluded.events_count,
  updated_at = now();
```

`session_id` 是主键，所以重复调用是幂等的：第二次调用会用最新算出的值覆盖。
注意 `started_at` 和 `created_at` **不在**更新列表里——它们只在第一次插入时确定。

## 4.5 `piano_attempts`：上传任务的状态机

```sql
create table public.piano_attempts (
  id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  learner_key uuid not null,
  song_id text not null, song_source text not null, song_title text,
  metadata jsonb not null default '{}',
  performance_key text not null, reference_key text not null,
  performance_hash text not null, reference_hash text not null,
  status text not null default 'CREATED'
    check(status in ('CREATED','UPLOADED','PROCESSING','READY','FAILED')),
  result_key text, alignment_run_id text, summary jsonb, error_code text,
  lease_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

状态流转：

```
   POST /attempts
        │
        ▼
    ┌────────┐  POST /finalize   ┌──────────┐   worker 领取   ┌────────────┐
    │CREATED │─────────────────▶ │ UPLOADED │───────────────▶│ PROCESSING │
    └────────┘   校验通过          └──────────┘                └─────┬──────┘
                                       ▲                            │
                                       │                     ┌──────┴──────┐
                                       │                     ▼             ▼
                                       │                ┌────────┐   ┌────────┐
                                       └────────────────│ FAILED │   │ READY  │
                                          worker 重试     └────────┘   └────────┘
```

`lease_until` 实现了一个简单的租约机制，防止两个 worker 同时处理同一个 attempt：

```sql
update public.piano_attempts
set status='PROCESSING', lease_until=now()+interval '10 minutes', updated_at=now()
where id=%s and (status in ('UPLOADED','FAILED')
                 or (status='PROCESSING' and lease_until<now()))
returning *
```

这条 UPDATE 是原子的。只有满足条件的那一行会被改，`returning *` 拿不到行就说明
「别人正在处理，且租约还没过期」。租约过期（10 分钟）后其他 worker 可以接管，
避免一个崩溃的 worker 永久占住任务。

`FAILED` 状态也在可领取列表里，所以失败的任务会被重试。

## 4.6 `piano_learner_keys`：伪名化

```sql
create table public.piano_learner_keys (
  user_id uuid primary key references auth.users(id) on delete cascade,
  learner_key uuid not null unique default gen_random_uuid()
);
```

所有落到 GCS 和数据湖里的数据用的都是 `learner_key`，不是 `user_id`。
这张映射表**不对浏览器公开**：

```sql
revoke all on public.piano_outbox, public.piano_learner_keys,
  public.piano_recommendation_feedback from anon, authenticated;
```

需要说清楚的是：**伪名化不等于匿名化**。持有这张映射表的人仍然可以把
数据湖里的记录还原到具体的人。它的作用是让分析系统在日常运行中不接触真实身份，
缩小泄露面，不是让数据变得不可追溯。

## 4.7 `challenge_recordings`：面向展示的视图

这张表是给前端用的，字段都是「用户想看的东西」：

```sql
create table if not exists public.challenge_recordings (
  id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  song_source text not null, song_id text not null, song_title text,
  duration_sec numeric not null,
  midi_storage_path text not null,
  created_at timestamptz not null default now(),
  accuracy_pct numeric not null default 0,
  difficulty numeric not null default 0
);
-- migration 005 追加
alter table public.challenge_recordings add column if not exists midi_keyboard_used boolean not null default false;
```

它由后端的 finalize 端点写入，`id` 直接复用 `attempt_id`：

```python
c.execute('''insert into public.challenge_recordings(
    id,user_id,song_source,song_id,song_title,duration_sec,
    midi_storage_path,midi_keyboard_used,accuracy_pct,difficulty)
  values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(id) do nothing''',
  (attempt_id, uid, row['song_source'], row['song_id'], row['song_title'],
   meta['duration_sec'], f'gcs:{attempt_id}',
   bool(settings.get('midi_keyboard_used',False)),
   settings.get('client_accuracy_pct') or 0,
   settings.get('client_difficulty') or 0))
```

`midi_storage_path` 写成 `gcs:{attempt_id}`——这个前缀就是前端下载时用来区分新旧存储的标记。

`accuracy_pct` 这里存的是**客户端算的**准确率，是个即时反馈值。
真正权威的准确率来自后端对齐（`piano_attempts.summary.accuracy`），
两者可能不一致，用途也不同：前者给用户看，后者用于分析。

`get_leaderboard` RPC 就是基于这张表做聚合的：

```sql
with agg as (
  select cr.user_id, count(*)::bigint as challenge_count,
         avg(cr.accuracy_pct) as accuracy_avg,
         coalesce(max(cr.difficulty), 0)::numeric as max_difficulty
  from public.challenge_recordings cr group by cr.user_id
), ordered as (
  select a.user_id, p.display_name, p.avatar_url, a.challenge_count, a.accuracy_avg, a.max_difficulty,
         row_number() over (order by case sort_by
             when 'accuracy' then a.accuracy_avg
             when 'difficulty' then a.max_difficulty
             else a.challenge_count end desc nulls last) as rn
  from agg a left join public.profiles p on p.id = a.user_id
)
select o.rn as rank, ... from ordered o order by o.rn;
```

`left join profiles` 是关键：即使某个用户没有 profile 行，他的成绩仍然会出现在榜上，
只是昵称头像为空。如果写成 inner join，缺 profile 的用户会整个消失。

## 4.8 两条链路的交汇点

总结一下它们在哪里发生关系：

1. **`user_play_logs.challenge_recording_id` → `challenge_recordings.id`**
   外键，`on delete set null`。这是唯一的显式引用。

2. **`challenge_recordings.id == piano_attempts.id`**
   同一个 UUID，但没有外键约束（两张表来自不同的 migration，且生命周期不同）。

3. **`piano_emit_play_event` 触发器**
   migration 009 给 `play_events_raw` 挂了一个触发器，让链路 A 的每条事件
   也进入链路 B 共用的 `piano_outbox`。详见第 6 章。

---

# 第 5 章 上传三段式

## 一句话版本

浏览器要把两个 MIDI 文件送到云端，但不是直接发给后端服务器。
而是先跟后端说「我要传这两个文件，它们的指纹是 XXX」，后端给两张一次性通行证，
浏览器拿着通行证直接把文件塞进云存储，最后再回来跟后端说「传完了，你验一下」。
这样做的好处是后端服务器不用承担文件流量。

## 5.1 为什么不直接 POST 给后端

最简单的做法是浏览器把 MIDI 内容 POST 给后端，后端再写进 GCS。这样有几个问题：

- Cloud Run 的请求体有大小限制，而且长时间上传会占住实例
- 文件流量走两遍网络（浏览器→后端→GCS），成本和延迟都翻倍
- 后端要处理分片、断点续传等一堆和业务无关的事

签名 URL 方案把文件流量从后端剥离出去。后端只处理很小的 JSON 请求，
真正的字节直接在浏览器和 GCS 之间流动。

## 5.2 第一段：`POST /attempts`

### 客户端准备

```ts
const performance = decode(params.midiBase64), reference = decode(params.referenceMidiBase64)
const performanceHash = await hash(performance), referenceHash = await hash(reference)
const key = `pianokt-upload:${params.sessionId ?? params.songId}:${performanceHash}:${referenceHash}`
const attemptId = sessionStorage.getItem(key) ?? crypto.randomUUID()
sessionStorage.setItem(key, attemptId)
```

`hash` 用的是浏览器原生的 Web Crypto：

```ts
async function hash(bytes: Uint8Array) {
  const value = await crypto.subtle.digest('SHA-256', new Uint8Array(bytes))
  return Array.from(new Uint8Array(value), b => b.toString(16).padStart(2, '0')).join('')
}
```

`sessionStorage` 那两行是**幂等键**。如果上传中途失败，用户重试时会复用同一个
`attempt_id`，而不是每次都创建新任务。缓存键包含两个文件的哈希，
所以只有「完全相同的内容」才会复用。成功 finalize 之后才清除：

```ts
const result = await backendRequest<{ id: string }>(`/attempts/${attemptId}/finalize`, {})
sessionStorage.removeItem(key)
```

### 请求的鉴权

所有后端请求都走同一个封装：

```ts
export async function backendRequest<T>(path: string, body?: unknown): Promise<T> {
  if (!apiRoot || !supabase) throw new Error('PianoKT backend is not configured')
  const { data: { session } } = await supabase.auth.getSession()
  if (!session) throw new Error('Not authenticated')
  const response = await fetch(apiRoot + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${session.access_token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`Backend ${response.status}: ${await response.text()}`)
  return response.json() as Promise<T>
}
```

带的是 Supabase 签发的 JWT。后端拿到后做两次校验：

```python
def user(authorization: str = Header(...)):
    if not authorization.startswith('Bearer '): raise HTTPException(401, 'Bearer token required')
    try:
        r = httpx.get(SUPABASE_URL + '/auth/v1/user',
                      headers={'Authorization': authorization, 'apikey': SUPABASE_ANON_KEY}, timeout=10)
        if r.status_code != 200: raise HTTPException(401, 'Invalid session')
        allowed = httpx.post(SUPABASE_URL + '/rest/v1/rpc/check_user_allowed',
                             headers={'Authorization': authorization, 'apikey': SUPABASE_ANON_KEY},
                             json={}, timeout=10)
        if allowed.status_code != 200: raise HTTPException(503, 'Access policy unavailable')
        if allowed.json() is not True: raise HTTPException(403, 'Account is not on the access list')
        return str(uuid.UUID(r.json()['id']))
    except httpx.HTTPError as exc: raise HTTPException(503, 'Auth unavailable') from exc
```

第一次确认 token 有效并取出 user ID，第二次执行白名单策略。
**user ID 来自 Supabase 的响应，不来自请求体**——请求模型里根本没有 `user_id` 字段。

### 输入校验

```python
class Upload(BaseModel):
    attempt_id: uuid.UUID
    song_id: str = Field(min_length=1, max_length=300)
    song_source: str = Field(max_length=50)
    song_title: str | None = Field(default=None, max_length=300)
    performance_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    reference_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    duration_sec: float = Field(gt=0, le=14400)
    session_id: uuid.UUID | None = None
    settings: dict = Field(default_factory=dict)
```

哈希字段有正则约束，必须是 64 位小写十六进制。时长上限 4 小时。
`settings` 是自由字典，但有额外检查：

```python
if len(json.dumps(body.settings)) > 16000: raise HTTPException(422, 'Settings too large')
for name, maximum in [('client_accuracy_pct',100), ('client_difficulty',10000), ('played_until_sec',14400)]:
    value = body.settings.get(name)
    if value is not None and (not isinstance(value,(int,float)) or not 0 <= value <= maximum):
        raise HTTPException(422, f'Invalid {name}')
```

### 建行与发签名 URL

```python
with connect() as c:
    c.execute('insert into public.piano_learner_keys(user_id) values(%s) on conflict do nothing', (uid,))
    learner = c.execute('select learner_key from public.piano_learner_keys where user_id=%s', (uid,)).fetchone()['learner_key']
    prefix = f'midi/{learner}/{body.attempt_id}'
    c.execute('''insert into public.piano_attempts(...) values(...) on conflict(id) do nothing''', (...))
    row = owned_attempt(c, body.attempt_id, uid)
    if not row or row['metadata'] != body.model_dump(mode='json'):
        raise HTTPException(409, 'Attempt identity conflict')
return dict(attempt_id=str(body.attempt_id),
            performance_url=signed(row['performance_key'],'PUT'),
            reference_url=signed(row['reference_key'],'PUT'))
```

`on conflict(id) do nothing` 加上后面的 metadata 比对，构成了一个完整的幂等语义：

- 第一次调用：插入新行，返回签名 URL
- 用相同参数重复调用：插入被忽略，读回来的 metadata 一致，正常返回新的签名 URL
- 用**相同 attempt_id 但不同参数**调用：metadata 不一致，返回 409

第三种情况是防篡改：不能用同一个 ID 偷偷换掉曲目或哈希。

### 签名 URL 的生成

```python
def signed(key, method):
    store = objects()
    if store.bucket is None: raise HTTPException(503, 'Signed uploads require GCS')
    kwargs = dict(version='v4', expiration=timedelta(minutes=10), method=method)
    if method == 'PUT':
        kwargs.update(content_type='audio/midi', headers={'x-goog-if-generation-match':'0'})
    if os.getenv('SIGNING_SERVICE_ACCOUNT'):
        import google.auth
        from google.auth.transport.requests import Request
        credentials, _ = google.auth.default(); credentials.refresh(Request())
        kwargs.update(service_account_email=os.environ['SIGNING_SERVICE_ACCOUNT'],
                      access_token=credentials.token)
    return store.bucket.blob(store._name(key)).generate_signed_url(**kwargs)
```

几个要点：

- **V4 签名，有效期 10 分钟。** 短有效期限制了 URL 泄露的影响窗口。
- **PUT 时锁定 `content_type='audio/midi'`。** 签名把 Content-Type 也算进去了，
  客户端必须发一模一样的头，否则签名校验失败。
- **`x-goog-if-generation-match: 0`** 是关键的一条。它的含义是
  「只有当这个对象不存在时才写入」。这让上传变成**创建专用**（create-only）操作：
  同一个 URL 用两次，第二次会返回 412 Precondition Failed。
- **`SIGNING_SERVICE_ACCOUNT` 的作用。** Cloud Run 的运行时身份没有私钥文件，
  没法本地签名。这段代码改用 IAM Credentials API 远程签名——
  拿当前身份的 access token，请求 IAM 代表指定服务账号签一个 URL。
  这需要该服务账号对自己有 `roles/iam.serviceAccountTokenCreator`，
  Terraform 里的 `google_service_account_iam_member.sign` 就是干这个的。

## 5.3 第二段：浏览器直传 GCS

```ts
for (const [url, bytes] of [[urls.performance_url, performance], [urls.reference_url, reference]] as const) {
  const response = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'audio/midi', 'x-goog-if-generation-match': '0' },
    body: new Uint8Array(bytes),
  })
  if (!response.ok && response.status !== 412) throw new Error(`MIDI upload failed: ${response.status}`)
}
```

注意 `response.status !== 412` 这个容忍。412 意味着「对象已存在」，
在重试场景下这是**正常且期望**的结果——第一次上传其实成功了，
只是后续步骤失败导致重试。既然对象已经在那里，就不该报错。

这一步是跨域请求，目标是 `storage.googleapis.com`。浏览器会先发 OPTIONS 预检，
raw 桶的 CORS 规则必须允许当前页面的 origin、PUT 方法，
以及 `x-goog-if-generation-match` 这个自定义头。任何一项不匹配都会得到
`TypeError: Failed to fetch`，而且看不到具体原因。

## 5.4 第三段：`POST /attempts/{id}/finalize`

后端此时才第一次接触文件内容：

```python
store = objects()
for kind in ('performance', 'reference'):
    blob = store.bucket.blob(store._name(row[kind+'_key']))
    try: blob.reload()
    except Exception as exc: raise HTTPException(409, 'Upload not complete') from exc
    if blob.size > 8_000_000: raise HTTPException(413, 'MIDI exceeds 8 MB')
    data = blob.download_as_bytes(if_generation_match=blob.generation)
    if digest(data) != row[kind+'_hash']: raise HTTPException(409, 'Upload digest mismatch')
    with TemporaryDirectory() as tmp:
        p = Path(tmp)/'input.mid'; p.write_bytes(data)
        try: check_midi(p)
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
```

四道检查：

1. **`blob.reload()`** — 对象存在吗？不存在说明浏览器那步没成功。
2. **大小上限 8 MB** — 防止资源耗尽。
3. **SHA-256 比对** — 实际存进去的字节和客户端声明的指纹一致吗？
   这挡住了「声明一个文件、实际传另一个」的攻击。
   `if_generation_match=blob.generation` 保证下载的和刚才 reload 看到的是同一个版本。
4. **`check_midi`** — 是不是合法 MIDI？

```python
def check_midi(path):
    if path.stat().st_size > 8_000_000: raise ValueError('MIDI exceeds 8 MB')
    with path.open('rb') as f:
        if f.read(4) != b'MThd': raise ValueError('Invalid MIDI header')
```

只检查魔数，是一个轻量的第一道防线。真正的解析在 worker 里做。

### 状态推进与三处写入

```python
with connect() as c:
    locked = c.execute('select * from public.piano_attempts where id=%s and user_id=%s for update',
                       (attempt_id, uid)).fetchone()
    if locked['status'] == 'CREATED':
        c.execute("update public.piano_attempts set status='UPLOADED', updated_at=now() where id=%s", (attempt_id,))
        payload = dict(event_type='performance.uploaded', attempt_id=str(attempt_id),
                       learner_key=str(row['learner_key']))
        c.execute('insert into public.piano_outbox(event_type,aggregate_id,payload) values(%s,%s,%s) on conflict do nothing',
                  ('performance.uploaded', str(attempt_id), Jsonb(payload)))
        meta = row['metadata']; settings = meta.get('settings', {})
        c.execute('''insert into public.challenge_recordings(...) values(...) on conflict(id) do nothing''', (...))
return {'id': str(attempt_id)}
```

`select … for update` 拿行锁，`if locked['status'] == 'CREATED'` 保证后面三个写入
**只执行一次**。重复调用 finalize 会走到这个 if 外面，直接返回成功——幂等。

三个写入在**同一个数据库事务**里：状态改成 UPLOADED、往 outbox 塞一条消息、
往 challenge_recordings 插一行。这个原子性很重要，是 Outbox 模式的基础，下一章详述。

## 5.5 完整时序

```
浏览器                      pianokt-api                 GCS raw 桶           Postgres
   │                            │                          │                   │
   │ 1. POST /attempts          │                          │                   │
   │   {attempt_id, hashes, …}  │                          │                   │
   ├───────────────────────────▶│                          │                   │
   │                            │ 校验 JWT + 白名单          │                   │
   │                            ├──────────────────────────┼──────────────────▶│
   │                            │ upsert learner_key       │                   │
   │                            │ insert piano_attempts    │                   │
   │                            │◀─────────────────────────┼───────────────────┤
   │                            │ 生成 2 个 V4 签名 URL      │                   │
   │◀───────────────────────────┤                          │                   │
   │  {performance_url,         │                          │                   │
   │   reference_url}           │                          │                   │
   │                            │                          │                   │
   │ 2. PUT performance.mid     │                          │                   │
   ├────────────────────────────┼─────────────────────────▶│                   │
   │    PUT reference.mid       │                          │ (if-gen-match: 0) │
   ├────────────────────────────┼─────────────────────────▶│                   │
   │                            │                          │                   │
   │ 3. POST /finalize          │                          │                   │
   ├───────────────────────────▶│                          │                   │
   │                            │ reload + download        │                   │
   │                            ├─────────────────────────▶│                   │
   │                            │◀─────────────────────────┤                   │
   │                            │ SHA-256 比对 + MIDI 头     │                   │
   │                            │                          │                   │
   │                            │ BEGIN                    │                   │
   │                            ├──────────────────────────┼──────────────────▶│
   │                            │  status = UPLOADED       │                   │
   │                            │  insert piano_outbox     │                   │
   │                            │  insert challenge_rec…   │                   │
   │                            │ COMMIT                   │                   │
   │◀───────────────────────────┤                          │                   │
   │  {id}                      │                          │                   │
```

---

# 第 6 章 Outbox 与 Relay

## 一句话版本

后端做完一件事之后需要通知别人。最直觉的做法是「存完数据库，顺手发个消息」，
但这两个动作可能一个成功一个失败，导致数据和消息对不上。
Outbox 的做法是：把「要发的消息」也当成数据存进同一张数据库，
和业务数据一起提交；然后由一个独立的搬运工定时把消息取出来真正发出去。

## 6.1 问题：双写不一致

设想没有 Outbox 的写法：

```python
# 反面教材，不要这样写
c.execute("update piano_attempts set status='UPLOADED' where id=%s", (attempt_id,))
c.commit()
publisher.publish(topic, message)   # ← 如果这里失败呢？
```

四种可能的失败组合：

| 数据库 | 消息 | 后果 |
|---|---|---|
| 成功 | 成功 | 正常 |
| 失败 | 失败 | 正常（什么都没发生） |
| **成功** | **失败** | **任务卡在 UPLOADED，永远没人处理** |
| **失败** | **成功** | **worker 去处理一个不存在的任务** |

后两种是真正的麻烦。第三种最常见——网络抖动、Pub/Sub 限流、进程被杀，
都会让消息发不出去，而数据库那边已经提交了。

调换顺序也不解决问题，只是把第三种换成第四种。
加重试也不彻底，因为进程可能在重试之前就死了。

## 6.2 解法：把消息也变成数据

Outbox 的核心洞察是：**只要消息和业务数据在同一个数据库里，就能靠事务保证它们同生共死。**

```sql
create table public.piano_outbox (
  event_id uuid primary key default gen_random_uuid(),
  event_type text not null,
  aggregate_id text not null,
  payload jsonb not null,
  published_at timestamptz,
  created_at timestamptz not null default now(),
  unique(event_type, aggregate_id)
);
create index piano_outbox_pending on public.piano_outbox(created_at) where published_at is null;
```

字段含义：

| 字段 | 作用 |
|---|---|
| `event_id` | 消息唯一 ID，会随消息一起发出去供消费方去重 |
| `event_type` | 消息种类，决定谁会收到它 |
| `aggregate_id` | 这条消息关于哪个业务实体 |
| `payload` | 消息正文，JSON |
| `published_at` | NULL 表示还没发出去 |

`unique(event_type, aggregate_id)` 这个约束意味着**同一个实体的同一类事件只能有一条**。
配合 `on conflict do nothing`，重复的业务操作不会产生重复消息。

`piano_outbox_pending` 是一个**部分索引**，只索引 `published_at is null` 的行。
已发布的消息（占绝大多数）不占索引空间，搬运工的扫描永远很快。

于是 finalize 里的写入变成了一个事务：

```python
with connect() as c:
    locked = c.execute('select * from piano_attempts where id=%s and user_id=%s for update', ...)
    if locked['status'] == 'CREATED':
        c.execute("update piano_attempts set status='UPLOADED' ...")
        c.execute('insert into piano_outbox(event_type,aggregate_id,payload) values(%s,%s,%s) on conflict do nothing',
                  ('performance.uploaded', str(attempt_id), Jsonb(payload)))
        c.execute('insert into challenge_recordings ...')
```

三个写入要么全成功、要么全回滚。**不可能出现「状态变了但消息没记下来」**。

## 6.3 Relay：搬运工

消息躺在表里不会自己飞出去，需要一个进程把它们取出来发到 Pub/Sub。
这就是 `pianokt-relay`：

```python
def relay(limit=100):
    from google.cloud import pubsub_v1
    publisher = pubsub_v1.PublisherClient(); count = 0
    for _ in range(limit):
        with connect() as c:
            row = c.execute('select * from public.piano_outbox where published_at is null '
                            'order by created_at for update skip locked limit 1').fetchone()
            if not row: break
            data = dict(row['payload'], event_id=str(row['event_id']), event_type=row['event_type'])
            publisher.publish(os.environ['EVENT_TOPIC'], json.dumps(data).encode(),
                              event_type=row['event_type']).result(timeout=30)
            c.execute('update public.piano_outbox set published_at=now() where event_id=%s', (row['event_id'],))
            count += 1
    return count
```

### `FOR UPDATE SKIP LOCKED` 的作用

这是整段代码里最值得理解的一句。

`FOR UPDATE` 会锁住选中的行，直到事务结束。如果只有 `FOR UPDATE`，
两个并发的 relay 实例会是这样：实例 A 锁住第一行开始处理，
实例 B 也想拿第一行，于是**阻塞等待**——它不会去看第二行，就干等着。
结果是并发度永远是 1，加实例没用。

`SKIP LOCKED` 改变了这个行为：**遇到已经被锁的行就跳过，去找下一个可用的**。
实例 B 会直接拿到第二行。这样多个实例可以真正并行工作，且不会处理到同一条消息。

`order by created_at` 保证大体上按时间顺序发送。注意是「大体上」——
并发情况下不保证严格有序，这是刻意的取舍：严格有序需要串行化，代价太大。

### 发布与标记的顺序

```python
publisher.publish(...).result(timeout=30)              # ① 先发
c.execute('update piano_outbox set published_at=now() ...')  # ② 后标记
```

`.result(timeout=30)` 是阻塞等待发布确认。只有 Pub/Sub 确认收到了，才去标记。

如果 ① 成功但 ② 之前进程崩了会怎样？`published_at` 还是 NULL，
下次 relay 会**再发一次**。这就是**至少一次投递**（at-least-once）：
消息不会丢，但可能重复。

反过来如果先标记再发，就变成了「至多一次」——可能丢消息。
在这个系统里，重复处理的代价远小于丢失，所以选择了至少一次。

### 重复怎么办：幂等消费

既然可能重复，消费方必须能容忍。worker 的处理逻辑天然是幂等的：

```python
row = c.execute("""update public.piano_attempts set status='PROCESSING', ...
    where id=%s and (status in ('UPLOADED','FAILED') or (status='PROCESSING' and lease_until<now()))
    returning *""", (attempt_id,)).fetchone()
if not row:
    current = c.execute('select status from public.piano_attempts where id=%s', (attempt_id,)).fetchone()
    if current and current['status'] == 'READY': return      # ← 已经处理过，直接返回成功
    raise RuntimeError('Attempt busy or not finalized')
```

已经是 READY 的任务收到重复消息时，**静默返回**，不重复计算也不报错。

更彻底的一层是对齐结果的内容寻址（见第 8 章）：`result.json` 的路径是输入内容的哈希，
所以即使真的重算一遍，写出来的也是同一个路径同样的内容，
`Objects.put` 的 `if_generation_match=0` 会发现对象已存在并比对字节：

```python
try: blob.upload_from_string(data, if_generation_match=0)
except PreconditionFailed:
    if blob.download_as_bytes() != data: raise ValueError('Immutable object conflict')
```

字节相同就当作成功，不同才报错。

## 6.4 触发器：行为事件也进 Outbox

migration 009 给 `play_events_raw` 挂了一个触发器，
让第 4 章讲的行为事件链路也接入这条消息管道：

```sql
create function public.piano_emit_play_event() returns trigger
language plpgsql security definer set search_path=public,pg_temp as $$
declare learner uuid;
begin
  insert into public.piano_learner_keys(user_id) values(new.user_id) on conflict do nothing;
  select learner_key into learner from public.piano_learner_keys where user_id=new.user_id;
  insert into public.piano_outbox(event_id,event_type,aggregate_id,payload)
  values(new.event_id,'practice.event',new.event_id::text,
    jsonb_build_object(
      'event_id',      new.event_id,
      'event_type',    'practice.event',
      'learner_key',   learner,
      'session_id',    new.session_id,
      'song_id',       new.song_id,
      'action',        new.event_type,      -- ← 注意这里
      'play_mode',     new.play_mode,
      'song_time_sec', new.song_time_sec,
      'client_ts',     new.client_ts,
      'received_at',   new.created_at))
  on conflict do nothing;
  return new;
end $$;

create trigger piano_play_outbox after insert on public.play_events_raw
  for each row execute function public.piano_emit_play_event();
```

三个设计细节：

**1. `event_type` 统一是 `'practice.event'`，真正的动作在 `payload->>'action'` 里。**

这是一个常见的困惑来源。你在 `piano_outbox` 表里 `select event_type` 会看到
一大片相同的 `practice.event`，看不出哪条是开始、哪条是暂停。要这样查：

```sql
select payload->>'action' as action, count(*)
from piano_outbox where event_type = 'practice.event'
group by 1;
```

这样设计是因为 Pub/Sub 的订阅过滤器是按消息属性做的，
把所有练习事件归为一类可以用一个订阅接收，再由消费端分流。

**2. 复用 `new.event_id` 作为 outbox 的 `event_id`。**

这让「一条原始事件」和「一条消息」严格一一对应。配合 `on conflict do nothing`，
即使触发器因为某种原因重复执行也不会产生重复消息。

**3. 写的是 `learner_key` 不是 `user_id`。**

伪名化在这里就完成了。流出数据库的消息里没有真实用户 ID。

**4. `security definer` + `revoke all from public`。**

```sql
revoke all on function public.piano_emit_play_event() from public;
```

函数以定义者身份运行（这样才能写 `piano_outbox`——普通用户对该表无任何权限），
但同时撤销了所有人的直接执行权限。只有触发器能调用它。

## 6.5 当前的实际状态

必须强调：**`schedules_paused = true` 意味着 relay 从来没有运行过。**

后果是可观测的：

```sql
select event_type, count(*), count(published_at) as published
from piano_outbox group by 1;
```

如果 `published` 全是 0，说明搬运工一次都没来过。消息在表里堆积，
下游的一切（worker 对齐、数据湖）都不会发生。

手动触发一次：

```bash
gcloud run jobs execute pianokt-relay --region us-central1 --project pianokt --wait
```

启用定时：把 tfvars 里的 `schedules_paused` 改成 `false` 再 apply，
或者直接 `gcloud scheduler jobs resume pianokt-relay --location us-central1`。

---

# 第 7 章 Pub/Sub 的三个订阅

## 一句话版本

消息发到一个叫「主题」的公告板上，然后有三个订阅者各自按自己的需求取走：
一个负责真正干活（做对齐），一个负责把所有消息存档到硬盘，
还有一个专门收留那些反复处理失败的消息，供人工排查。

## 7.1 拓扑

```
                    ┌─────────────────────────┐
                    │  topic: pianokt-events  │
                    └────────────┬────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
        ▼                        ▼                        ▼
┌──────────────────┐  ┌────────────────────┐   （投递失败 5 次后）
│ pianokt-         │  │ pianokt-events-    │              │
│ alignment-worker │  │ archive            │              ▼
│                  │  │                    │  ┌────────────────────────┐
│ filter:          │  │ 无 filter，收全部    │  │ topic: pianokt-        │
│  event_type =    │  │                    │  │        dead-letter     │
│  performance.    │  │ 写 GCS：            │  └───────────┬────────────┘
│  uploaded        │  │  events/*.jsonl    │              │
│                  │  │  每 60 秒一个文件    │              ▼
│ push (OIDC) →    │  │                    │  ┌────────────────────────┐
│  pianokt-worker  │  │                    │  │ pianokt-dead-letter-   │
│  /pubsub         │  │                    │  │ inspection             │
│                  │  │                    │  │ 保留 7 天，供人工检查     │
│ ack: 600s        │  │                    │  └────────────────────────┘
│ retry: 30s~600s  │  │                    │
└──────────────────┘  └────────────────────┘
```

## 7.2 订阅一：alignment-worker

```hcl
resource "google_pubsub_subscription" "worker" {
  name                 = "pianokt-alignment-worker"
  topic                = google_pubsub_topic.events.id
  filter               = "attributes.event_type = \"performance.uploaded\""
  ack_deadline_seconds = 600
  expiration_policy { ttl = "" }
  push_config {
    push_endpoint = "${google_cloud_run_v2_service.app["worker"].uri}/pubsub"
    oidc_token { service_account_email = google_service_account.runtime["push"].email }
  }
  retry_policy {
    minimum_backoff = "30s"
    maximum_backoff = "600s"
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead.id
    max_delivery_attempts = 5
  }
}
```

逐项拆解：

### `filter`

```
attributes.event_type = "performance.uploaded"
```

过滤的是**消息属性**，不是消息正文。relay 发布时把类型同时放进了属性：

```python
publisher.publish(os.environ['EVENT_TOPIC'], json.dumps(data).encode(),
                  event_type=row['event_type'])
                # ↑ 这个关键字参数变成消息属性
```

这个过滤在 Pub/Sub 服务端执行，不匹配的消息**根本不会投递**给这个订阅，
也不产生费用。所以 `practice.event` 那一大堆行为事件不会打扰 worker。

### `ack_deadline_seconds = 600`

worker 有 10 分钟时间处理并返回 200。超时未确认，Pub/Sub 会认为投递失败并重试。
对齐算法在大曲子上可能跑几十秒，600 秒留了充足余量。

注意这个值和 worker 的 Cloud Run 超时是配套的：

```hcl
timeout = "540s"                                   # Cloud Run 服务超时 9 分钟
max_instance_request_concurrency = each.value == "worker" ? 1 : 20
```

`concurrency = 1` 意味着**一个 worker 实例同时只处理一条消息**。
对齐是 CPU 密集的，并发处理会互相拖慢并可能耗尽内存（限额 2 GiB）。

worker_api 内部还有一层超时：

```python
subprocess.run([sys.executable,'-m','pianokt_backend.cli','worker',attempt], check=True, timeout=480)
```

480 < 540 < 600，三层超时依次收紧，保证内层先超时并留下可读的错误。

### `push_config` 与 OIDC

推送模式意味着 **Pub/Sub 主动 HTTP POST 到 worker**，而不是 worker 去拉取。
好处是 Cloud Run 可以缩容到 0，有消息才被唤醒。

`oidc_token` 让 Pub/Sub 在请求里带一个身份令牌，证明「我是 pianokt-push 这个服务账号」。
Cloud Run 那边只允许这个身份调用：

```hcl
resource "google_cloud_run_v2_service_iam_member" "worker" {
  name   = google_cloud_run_v2_service.app["worker"].name
  role   = "roles/run.invoker"
  member = "serviceAccount:${google_service_account.runtime["push"].email}"
}
```

对比 api 服务：

```hcl
resource "google_cloud_run_v2_service_iam_member" "api" {
  name   = google_cloud_run_v2_service.app["api"].name
  role   = "roles/run.invoker"
  member = "allUsers"          # ← 公开
}
```

**这是一条重要的安全边界。** worker 的代码里没有任何鉴权逻辑：

```python
@app.post('/pubsub')
def receive(envelope: dict):
    try:
        data = json.loads(base64.b64decode(envelope['message']['data'], validate=True))
        if data['event_type'] == 'performance.uploaded':
            attempt = str(uuid.UUID(data['attempt_id']))
            subprocess.run([sys.executable,'-m','pianokt_backend.cli','worker',attempt], check=True, timeout=480)
    except Exception as exc:
        raise HTTPException(503, 'Worker failed; retry or inspect dead letter queue') from exc
    return {'ok': True}
```

它完全依赖 Cloud Run 的 IAM 来验证调用者。文件头的注释写得很明确：

> Deploy ONLY as private Cloud Run service with IAM-authenticated Pub/Sub push.

**如果把 worker 设成 allUsers，任何人都可以任意触发任意 attempt 的处理。**
这一点不能改。

### `retry_policy`

```
minimum_backoff = "30s"
maximum_backoff = "600s"
```

失败后等 30 秒重试，如果继续失败，等待时间指数增长直到 600 秒封顶。
退避的意义是给下游恢复的时间——如果数据库正好在重启，
连续快速重试只会加重负担。

### `expiration_policy { ttl = "" }`

空字符串表示**永不过期**。Pub/Sub 默认会把 31 天没有活动的订阅自动删除，
这里显式关掉了。

## 7.3 死信队列：pianokt-dead-letter

```hcl
dead_letter_policy {
  dead_letter_topic     = google_pubsub_topic.dead.id
  max_delivery_attempts = 5
}
```

### 它解决什么问题

设想一条消息因为某种原因永远处理不成功——比如 MIDI 文件损坏，
worker 每次都抛异常。没有死信机制的话，Pub/Sub 会**无限重试**：

- 这条消息永远占着位置，反复消耗 worker 的算力
- 日志被同一个错误刷屏，真正的问题被淹没
- 最坏情况下形成「毒丸消息」，拖垮整个订阅的吞吐

死信策略给重试设了一个上限。投递 5 次仍未被确认，Pub/Sub 就**放弃**，
把这条消息转发到 `pianokt-dead-letter` 主题。原订阅继续处理后面的消息，不受影响。

### 死信的消息去了哪

死信主题上挂了一个专门的订阅：

```hcl
resource "google_pubsub_subscription" "dead" {
  name                       = "pianokt-dead-letter-inspection"
  topic                      = google_pubsub_topic.dead.id
  message_retention_duration = "604800s"     # 7 天
}
```

这是一个**拉取式**订阅（没有 push_config），消息在里面躺 7 天等人来看。
它不会自动处理任何东西——死信队列的意义就是「人工介入」。

查看死信：

```bash
gcloud pubsub subscriptions pull pianokt-dead-letter-inspection \
  --limit 10 --auto-ack=false --project pianokt --format=json
```

`--auto-ack=false` 很重要，否则看一眼消息就没了。

### 需要的额外权限

死信机制要求 Pub/Sub 服务身份能做两件事：往死信主题发布、从原订阅确认消息。
Terraform 里对应两条：

```hcl
resource "google_pubsub_topic_iam_member" "dead_publish" {
  topic  = google_pubsub_topic.dead.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
resource "google_pubsub_subscription_iam_member" "dead_subscribe" {
  subscription = google_pubsub_subscription.worker.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
```

漏掉任何一条，死信策略会静默失效——消息继续无限重试，
而你在控制台上看到策略是「已配置」的。这是一个很隐蔽的坑。

## 7.4 订阅二：events-archive

```hcl
resource "google_pubsub_subscription" "archive" {
  name  = "pianokt-events-archive"
  topic = google_pubsub_topic.events.id
  expiration_policy { ttl = "" }
  cloud_storage_config {
    bucket          = google_storage_bucket.data["raw"].name
    filename_prefix = "events/"
    filename_suffix = ".jsonl"
    max_duration    = "60s"
  }
  depends_on = [google_storage_bucket_iam_member.archive_create,
                google_storage_bucket_iam_member.archive_bucket]
}
```

这是一个 **Cloud Storage 订阅**——Pub/Sub 托管的功能，
不需要任何自己的代码，它会直接把消息写成文件落到 GCS。

**没有 filter**，所以它收到主题上的**每一条**消息，包括 `performance.uploaded`
和所有 `practice.event`。

`max_duration = "60s"` 表示每积攒 60 秒的消息写一个文件。
文件名形如 `events/2026-09-12T00:00:00+00:00_2026-09-12T00:01:00+00:00_xxxx.jsonl`，
内容是每行一个 JSON。

这个订阅是**数据湖行为事件的唯一数据源**。第 10 章会讲到 Spark 作业直接读
`gs://…-raw/events/`，形成一个闭环：

```
play_events_raw (Postgres)
    ↓ 触发器
piano_outbox (Postgres)
    ↓ relay
pianokt-events (Pub/Sub)
    ↓ archive 订阅
events/*.jsonl (GCS raw)
    ↓ Spark
bronze/silver/practice_events (GCS lake, Delta)
```

### 需要的权限

Pub/Sub 的服务身份要能写这个桶：

```hcl
resource "google_storage_bucket_iam_member" "archive_create" {
  bucket = google_storage_bucket.data["raw"].name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
resource "google_storage_bucket_iam_member" "archive_bucket" {
  bucket = google_storage_bucket.data["raw"].name
  role   = "roles/storage.legacyBucketReader"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}
```

`legacyBucketReader` 看起来奇怪，但 Cloud Storage 订阅确实需要它来读取桶的元数据。
`depends_on` 保证这两条 IAM 先于订阅创建，否则订阅创建时会因权限不足而失败。

## 7.5 消息的实际形状

relay 发出去的消息正文：

```python
data = dict(row['payload'], event_id=str(row['event_id']), event_type=row['event_type'])
```

也就是 outbox 的 payload 加上两个字段。三种消息类型：

**`performance.uploaded`**（来自 API finalize）

```json
{
  "event_type": "performance.uploaded",
  "attempt_id": "3f2a…",
  "learner_key": "8c1b…",
  "event_id": "d4e5…"
}
```

**`alignment.completed`**（来自 worker）

```json
{
  "event_type": "alignment.completed",
  "attempt_id": "3f2a…",
  "alignment_run_id": "a91f…",
  "result_key": "alignment/a91f…/result.json",
  "event_id": "7b2c…"
}
```

**`practice.event`**（来自数据库触发器）

```json
{
  "event_id": "e1f2…",
  "event_type": "practice.event",
  "learner_key": "8c1b…",
  "session_id": "5a6b…",
  "song_id": "sz6O_i",
  "action": "play_started",
  "play_mode": "challenge",
  "song_time_sec": 0,
  "client_ts": "2026-09-12T00:13:45.120Z",
  "received_at": "2026-09-12T00:13:45.331Z"
}
```

注意 `alignment.completed` 目前**没有任何订阅消费它**。它被 archive 订阅存档，
但没有 worker 去响应。这是留给未来扩展的接口（比如完成后推送通知）。

---

# 第 8 章 对齐是怎么算的

## 一句话版本

系统拿到两份 MIDI：学生弹的和标准答案。它先用一个音符匹配算法把两者配对，
然后校正整体的时间偏移（学生可能整首都慢半拍，这不该算成每个音都错），
最后逐个音符判定：弹对了、弹错音了、太早、太晚、漏了、多了。

## 8.1 输入与不变量

worker 从 GCS 取回两个文件并再次校验完整性：

```python
with TemporaryDirectory(prefix='pianokt-worker-') as tmp:
    files = {}
    for kind in ('performance', 'reference'):
        data = store.read(row[kind+'_key'])
        if digest(data) != row[kind+'_hash']: raise ValueError('MIDI integrity mismatch')
        files[kind] = Path(tmp)/(kind+'.mid'); files[kind].write_bytes(data)
    result = align(files['reference'], files['performance'], attempt_id,
                   stage_sink=lambda run, data: store.json(f'matching/{run}/matches.json', data))
```

这是**第二次**校验哈希（第一次在 API finalize 里）。看似冗余，
但它保证了「worker 处理的字节」和「客户端声明的字节」严格一致，
即使中间存储发生了任何异常。

## 8.2 配置：`AlignmentConfig`

```python
@dataclass(frozen=True)
class AlignmentConfig:
    version: str = 'dual-dtw-global-offset-v1'
    timing_threshold_ms: int = 150
    insertion_attach_window_sec: float = 0.25
    minimum_anchors: int = 3
    max_notes: int = 10000

    @property
    def identity(self): return asdict(self)
```

| 参数 | 值 | 含义 |
|---|---|---|
| `version` | `dual-dtw-global-offset-v1` | 算法版本标识，写进每条结果 |
| `timing_threshold_ms` | 150 | 超过这个偏差算「太早/太晚」 |
| `insertion_attach_window_sec` | 0.25 | 多余音符归属到哪只手的时间窗 |
| `minimum_anchors` | 3 | 估计时间偏移所需的最少锚点数 |
| `max_notes` | 10000 | 单个 MIDI 的音符上限 |

`frozen=True` 让它不可变，`identity` 把整个配置序列化——
它会参与 run ID 的计算，所以**改任何一个参数都会产生全新的 run ID**，
旧结果不会被覆盖。

## 8.3 run ID：内容寻址

```python
run_id = stable_id(attempt_id, score_hash, performance_hash, config.identity, matcher_version)
```

```python
def stable_id(*parts):
    return digest(json.dumps(parts, sort_keys=True, separators=(',',':')).encode())
def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()
```

五个输入决定了 run ID：任务 ID、标准答案哈希、演奏哈希、算法配置、匹配器版本。

这带来几个很好的性质：

- **相同输入必然得到相同的 run ID**，因此结果路径也相同。
  重复计算写出的是同一个文件，靠 `if_generation_match=0` 天然去重。
- **任何一个输入变了，run ID 就变**。升级 parangonar 版本或调整阈值后，
  同一份演奏会产生一个新的 run，旧结果原样保留。这让结果**不可变且可追溯**。
- 你随时可以回答「这个数字是用哪个版本的算法、在哪份输入上算出来的」。

worker 里有一段专门利用这个性质做崩溃恢复：

```python
if result is None:
    # Recover a crash between writing the immutable result and its manifest.
    from importlib.metadata import version
    run = stable_id(attempt_id, row['reference_hash'], row['performance_hash'],
                    AlignmentConfig().identity, version('parangonar'))
    result_key = f'alignment/{run}/result.json'
    try:
        result = json.loads(store.read(result_key))
        store.json(marker_key, {'result_key': result_key})
    except FileNotFoundError: result = None
```

如果进程在「写完 result.json」和「写 manifest」之间崩溃，
重启后可以**重新推导**出 run ID 并去找那个文件，避免重算。

## 8.4 第一步：parangonar 匹配

```python
import parangonar as pa
matcher_version = version('parangonar')
with TemporaryDirectory(prefix='pianokt-align-') as tmp:
    out = Path(tmp)/'matches.csv'
    pa.match_midis(ref_midi=str(reference), performance_midi=str(performance),
                   output_file=str(out), shift_onsets_to_zero=False)
    raw = pd.read_csv(out)
```

`parangonar==3.3.3` 是一个专门做乐谱-演奏对齐的库。它输出一张表，
每行是一个「对齐单元」，`alignment_type` 有三种取值：

| 类型 | 含义 |
|---|---|
| `match` | 标准答案里的某个音，学生弹了对应的音 |
| `deletion` | 标准答案里有，学生没弹（漏音） |
| `insertion` | 学生弹了，标准答案里没有（多音） |

`shift_onsets_to_zero=False` 很关键——**不要**把起始时间归零。
学生开头的犹豫是有意义的信息，归零会把它抹掉。

匹配完立刻把原始输出存档：

```python
if stage_sink: stage_sink(run_id, dict(attempt_id=attempt_id, score_version=score_hash,
    performance_hash=performance_hash, config=config.identity,
    matcher_version=matcher_version, raw_matches=raw_records))
```

写到 `matching/{run_id}/matches.json`。这份中间产物让你能在不重跑算法的情况下
调试后续的判定逻辑。

然后做 schema 校验：

```python
required = {'alignment_type','ref_pitch','performance_pitch','ref_onset_sec','performance_onset_sec','ref_track'}
if not required <= set(raw.columns): raise ValueError('Matcher schema mismatch')
if not raw.alignment_type.isin(['match','insertion','deletion']).all(): raise ValueError('Unknown alignment type')
```

这是防止 parangonar 升级后悄悄改了输出格式而不被发现。

## 8.5 第二步：还原音轨编号

parangonar 内部用 Partitura 解析 MIDI，而 Partitura 会**重新编号音轨**。
这意味着输出里的 `ref_track` 不是原始 MIDI 的音轨索引，
而左右手判定恰恰依赖原始索引。

`alignment/tracks.py` 通过内容指纹把它映射回去：

```python
def restore_reference_tracks(frame, reference):
    signatures = defaultdict(set)
    midi = mido.MidiFile(str(reference))
    for track_id, track in enumerate(midi.tracks):
        tick = 0
        for message in track:
            tick += message.time
            if message.type == 'note_on' and message.velocity > 0:
                signatures[(tick, message.note, message.channel, message.velocity)].add(track_id)
    result = frame.copy(); result['ref_matcher_track'] = result.ref_track
    mapping = {}
    for track, rows in result.loc[result.ref_track.notna()].groupby('ref_track'):
        candidates = None
        for _, row in rows.iterrows():
            signature = tuple(int(row['ref_'+k]) for k in ('onset_tick','pitch','channel','velocity'))
            matches = signatures.get(signature, set())
            candidates = matches.copy() if candidates is None else candidates & matches
        if not candidates or len(candidates) != 1:
            raise ValueError('Ambiguous reference track mapping; use explicit distinct score tracks')
        mapping[track] = next(iter(candidates))
    result['ref_track'] = result.ref_track.map(mapping)
    return result
```

思路是：用 `(tick, 音高, 通道, 力度)` 四元组作为每个音符的指纹，
建立「指纹 → 原始音轨集合」的索引。然后对匹配器给出的每个音轨，
取它所有音符的候选集合求**交集**。如果交集恰好是一个音轨，映射成立。

交集为空或多于一个就抛错，宁可失败也不做错误的猜测。这种情况通常意味着
两个音轨的内容完全重复，此时应该在源头保证乐谱音轨可区分。

## 8.6 第三步：全局时间偏移校正

这一步解决一个非常实际的问题：**学生整首曲子都晚了 0.3 秒**。

如果不校正，每一个音符的时间偏差都是 0.3 秒，全部超过 150 毫秒阈值，
判定结果会是「一个音都没弹准」。但实际上他弹得很稳，只是起拍晚了。

```python
def correct_offset(frame, minimum_anchors=3):
    df = frame.copy()
    anchors = df.loc[(df.alignment_type=='match')
                     & df.ref_onset_sec.notna() & df.performance_onset_sec.notna()
                     & (df.ref_pitch==df.performance_pitch)].copy()
    anchors['group'] = anchors.ref_onset_sec.round(3)
    grouped = anchors.groupby('group')[['ref_onset_sec','performance_onset_sec']].median()
    if len(grouped) < minimum_anchors: raise InsufficientAnchors('Too few distinct matched onsets')
    delta = (grouped.performance_onset_sec - grouped.ref_onset_sec).to_numpy()
    center = float(np.median(delta)); mad = float(np.median(np.abs(delta-center)))
    kept = delta[np.abs(delta-center) <= max(0.25, 3*1.4826*mad)]
    if len(kept) < minimum_anchors: raise InsufficientAnchors('Too few reliable timing anchors')
    offset = float(np.median(kept))
    df['performance_onset_sec_global_aligned'] = df.performance_onset_sec - offset
    df['timing_deviation_global_aligned'] = df.performance_onset_sec_global_aligned - df.ref_onset_sec
    return df, dict(global_offset_sec=offset, anchors_total=len(delta),
                    anchors_kept=len(kept), mad_sec=mad)
```

逐步解释：

**1. 挑锚点。** 只用「匹配上的 + 音高完全正确」的音符。
音高都弹错了的音符，它的时间信息不可信。

**2. 按起始时刻分组取中位数。** `round(3)` 把同一个和弦里的音（起始时间相同）
归为一组，避免和弦的音数影响权重——一个 5 音和弦不该比单音有 5 倍话语权。

**3. 用 MAD 剔除离群值。** `mad = median(|delta - center|)` 是中位绝对偏差，
比标准差更抗离群。`1.4826` 是让 MAD 在正态分布下等价于标准差的换算系数，
所以 `3 * 1.4826 * mad` 大致相当于「3 个标准差」。
`max(0.25, …)` 保证阈值不会因为演奏太稳而收得过窄。

这一步的作用是：学生中间某处大停顿导致的巨大偏差不会污染全局偏移的估计。

**4. 取中位数作为偏移量。** 然后所有音符的时间都减去它，得到
`performance_onset_sec_global_aligned`，再与标准答案相减得到
`timing_deviation_global_aligned`——这才是判定用的偏差。

**5. 锚点不够就拒绝。** `InsufficientAnchors` 是一个显式的失败：

```python
class InsufficientAnchors(ValueError):
    """Timing cannot be estimated reliably; preserve inputs for review."""
```

如果学生只弹对了 2 个音，系统**不会**硬给一个偏移量，而是承认「算不了」。
输入文件保留下来供人工检查。

诊断信息会写进结果的 `diagnostics` 字段：

```json
{"global_offset_sec": 0.312, "anchors_total": 48, "anchors_kept": 45, "mad_sec": 0.021}
```

`anchors_total` 和 `anchors_kept` 的差距能告诉你演奏有多不稳。

## 8.7 第四步：逐音符判定

### 错误分类表

```python
REJECT_REASON = {
    -1: "not_required",                 # 这只手这个时刻不需要弹
     0: "correct",                      # 正确
     1: "missing_note",                 # 漏音
     2: "extra_note",                   # 多音
     3: "wrong_pitch_or_substitution",  # 音高错误 / 替换
     4: "too_early",                    # 太早
     5: "too_late",                     # 太晚
     6: "multiple_or_other_error",      # 多种错误叠加
}
```

### 音符级判定

```python
def classify_matched_note(row, ref_pitch_col, perf_pitch_col, timing_dev_col) -> int:
    errors: list[int] = []
    if pd.notna(row.get(ref_pitch_col)) and pd.notna(row.get(perf_pitch_col)):
        pitch_offset = int(round(row[perf_pitch_col] - row[ref_pitch_col]))
        if pitch_offset != 0: errors.append(3)
    if pd.notna(row.get(timing_dev_col)):
        timing_ms = float(row[timing_dev_col]) * 1000
        if timing_ms < -TIMING_THRESHOLD_MS: errors.append(4)
        elif timing_ms > TIMING_THRESHOLD_MS: errors.append(5)
    if len(errors) == 0: return 0
    if len(errors) == 1: return errors[0]
    return 6
```

关键点：**多种错误叠加时不保留细节，统一归为 6**。
「又弹错音又弹晚了」不会被记成 3 或 5，而是 6。
这样分类是互斥的，统计时不会重复计数。

上层调用把 deletion / insertion 直接映射成 1 / 2：

```python
df['reject_reason'] = [
    1 if r.alignment_type == 'deletion' else
    2 if r.alignment_type == 'insertion' else
    labels.classify_matched_note(r, 'ref_pitch', 'performance_pitch', 'timing_deviation_global_aligned')
    for _, r in df.iterrows()
]
df['is_correct'] = df.reject_reason == 0
```

### 手级合并

同一只手在同一时刻可能有多个音（和弦）。这些音符级判定要合并成一个手级判定：

```python
def combine_reject_reasons(reasons: list[int | None]) -> int:
    reasons = [r for r in reasons if r is not None and r != -1]
    if len(reasons) == 0: return -1
    nonzero = [r for r in reasons if r != 0]
    if len(nonzero) == 0: return 0
    # 同一只手同一时刻既有 deletion 又有 insertion，视为音高替换。
    if 1 in nonzero and 2 in nonzero:
        other_errors = [r for r in nonzero if r not in (1, 2)]
        if len(other_errors) == 0: return 3
        return 6
    unique_errors = sorted(set(nonzero))
    if len(unique_errors) == 1: return unique_errors[0]
    return 6
```

中间那段特判很有意思：如果同一只手同一时刻既漏了一个音（1）又多了一个音（2），
最合理的解释是**学生弹错了音高**——他按了一个键，只是按错了位置。
所以合并成 3（替换）而不是同时报漏音和多音。

## 8.8 结果的可信度标记

worker 在拿到对齐结果后，会给每个音符补两个标记：

```python
settings = row['metadata'].get('settings', {})
end = settings.get('played_until_sec')
start = (settings.get('range') or {}).get('start', 0)
for note in result['notes']:
    note['within_observed_range'] = end is None or note.get('ref_onset_sec') is None \
                                    or note['ref_onset_sec'] <= end - start
    note['timing_labels_valid'] = not settings.get('waiting', False)
```

**`within_observed_range`** — 这个音符在用户实际弹到的范围内吗？

如果用户弹到一半就退出，参考谱里后半段的音符全都会被标记成「漏音」。
但那不是学生的错，他只是没弹到那里。这个标记把「没弹到」和「弹漏了」区分开。

**`timing_labels_valid`** — 时间标签可信吗？

等待模式下播放器会停下来等学生弹对，时间轴的含义完全变了。
这时所有时间相关的判定（太早 / 太晚）都没有意义。

汇总统计只算范围内的音符：

```python
observed = [n for n in result['notes'] if n['within_observed_range']]
expected = sum(n['alignment_type'] in ('match','deletion') for n in observed)
correct  = sum(n['is_correct'] for n in observed)
result['summary'].update(
    expected_notes=expected, correct_notes=correct,
    accuracy=correct/expected if expected else None,
    missing_notes=sum(n['alignment_type']=='deletion' for n in observed),
    timing_labels_valid=not settings.get('waiting', False))
```

`accuracy` 的分母是「应该弹的音数」（match + deletion），
不包括多弹的音（insertion）。多弹的音会体现在 `extra_notes` 里，不稀释准确率。

`expected` 为 0 时 `accuracy` 是 `None` 而不是 0——**没弹和弹错是两回事**。

## 8.9 训练数据导出的过滤

`pipeline/training.py` 展示了这些标记的实际用途：

```python
if settings.get('waiting'): continue          # 排除等待模式
end = settings.get('played_until_sec', float('inf')) - (settings.get('range') or {}).get('start', 0)
for hand, a in r['events_data']['data'].items():
    for i, reason in enumerate(a['reject_reason']):
        if reason == -1 or a['onset_time'][i] > end*1000: continue    # 排除不需要弹的和超出范围的
        examples.append(dict(learner_key=…, hand=hand, onset_ms=a['onset_time'][i],
                             pitches=a['pitches'][i], correct=int(reason==0),
                             reject_reason=reason, attempt_created_at=…))
```

还有一道版本一致性检查：

```python
versions = {}
for e in examples: versions.setdefault(e['attempt_id'], set()).add(e['alignment_run_id'])
if any(len(v) > 1 for v in versions.values()):
    raise ValueError('Select one alignment version per attempt before training')
```

同一次演奏如果因为算法升级产生了多个 run，**必须显式选一个**，
不能混着用。否则训练集里会有同一份数据的两种标注。

输出里带一个诚实的警告：

```python
payload = dict(dataset_version=stable_id(cutoff, examples), cutoff=cutoff, examples=examples,
               warning='Split students/time before windowing. No trained model is included.')
```

意思是：做训练/验证划分时要**按学生和时间切分**，不能随机切。
随机切会让同一个学生的数据同时出现在训练集和测试集里，造成信息泄露。

## 8.10 结果 JSON 的完整结构

```python
return dict(
    schema_version=1,
    alignment_run_id=run_id,
    attempt_id=attempt_id,
    score_version=score_hash,
    performance_hash=performance_hash,
    config=config.identity,
    matcher_version=matcher_version,
    diagnostics=diagnostics,
    summary=summary,
    raw_matches=raw_records,
    notes=json.loads(df.to_json(orient='records')),
    events_data=hands,
)
```

worker 再补上五个字段：

```python
result.update(learner_key=str(row['learner_key']), song_id=row['song_id'],
              practice_metadata=row['metadata'],
              attempt_created_at=row['created_at'].isoformat(),
              alignment_completed_at=datetime.now(timezone.utc).isoformat())
```

| 顶层字段 | 内容 |
|---|---|
| `schema_version` | 结构版本，目前是 1 |
| `alignment_run_id` | 内容寻址的 run ID |
| `attempt_id` / `learner_key` / `song_id` | 归属信息 |
| `score_version` | 参考谱的 SHA-256 |
| `performance_hash` | 演奏的 SHA-256 |
| `config` | 完整的算法配置 |
| `matcher_version` | parangonar 版本号 |
| `diagnostics` | 偏移校正的诊断信息 |
| `summary` | 汇总统计 |
| `raw_matches` | parangonar 的原始输出 |
| `notes` | 音符级判定，每个音一条 |
| `events_data` | 手级事件，按左右手分组的数组 |
| `practice_metadata` | 客户端上报的练习设置 |
| `attempt_created_at` / `alignment_completed_at` | 时间戳 |

`events_data` 的结构是**列式**的（每个字段一个数组），而不是行式：

```json
{
  "data": {
    "left":  { "reject_reason": [0,0,3,…], "timing_offset": […], "pitch_offset": […],
               "duration": […], "onset_time": […], "pitches": [[60],[64,67],…] },
    "right": { … }
  }
}
```

这种格式对下游做向量化处理更友好，代价是可读性差一些。

---

# 第 9 章 GCS 每一层路径

## 一句话版本

云存储里有三个桶。第一个装原始素材（MIDI 文件、算法结果、事件日志），
第二个装整理好的分析表，第三个只装一个「读到哪了」的书签。
这一章把每个桶下面的每个文件夹讲清楚：谁写进去、谁读出来、里面长什么样。

## 9.1 三个桶的公共配置

```hcl
resource "google_storage_bucket" "data" {
  for_each                    = toset(["raw", "lake", "checkpoint"])
  name                        = "${var.project_id}-pianokt-${each.value}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  ...
}
```

实际名字（`project_id = "pianokt"`）：

- `pianokt-pianokt-raw`
- `pianokt-pianokt-lake`
- `pianokt-pianokt-checkpoint`

三项安全配置的含义：

**`uniform_bucket_level_access = true`** — 关闭对象级 ACL，权限只能通过 IAM 管理。
这避免了「桶权限看起来没问题，但某个对象被单独设成了公开」这类难以审计的情况。

**`public_access_prevention = "enforced"`** — 从组织层面禁止任何公开访问。
即使有人误配了 `allUsers` 的 IAM 绑定，也会被这一层拦下。

**`force_destroy = false`** — `terraform destroy` 时如果桶里还有对象，
Terraform 会**拒绝删除**而不是清空。防止误操作导致数据永久丢失。

只有 raw 桶有 CORS 规则（第 2 章已讲），因为只有它需要接受浏览器直传。

## 9.2 raw 桶：五个前缀

`RAW_ROOT = gs://pianokt-pianokt-raw`

```
gs://pianokt-pianokt-raw/
│
├── midi/                       ← 原始 MIDI 文件
│   └── {learner_key}/
│       └── {attempt_id}/
│           ├── performance.mid
│           └── reference.mid
│
├── matching/                   ← 匹配器的原始输出（调试用）
│   └── {alignment_run_id}/
│       └── matches.json
│
├── alignment/                  ← 对齐结果（不可变，内容寻址）
│   └── {alignment_run_id}/
│       └── result.json
│
├── attempt-results/            ← attempt → run 的指针
│   └── {attempt_id}/
│       └── manifest.json
│
└── events/                     ← Pub/Sub 事件归档
    └── {start}_{end}_{uuid}.jsonl
```

### `midi/{learner_key}/{attempt_id}/`

**谁写：** 浏览器，通过 API 签发的签名 URL 直传。

路径在 API 里拼装：

```python
learner = c.execute('select learner_key from public.piano_learner_keys where user_id=%s', (uid,)).fetchone()['learner_key']
prefix = f'midi/{learner}/{body.attempt_id}'
# performance_key = prefix + '/performance.mid'
# reference_key   = prefix + '/reference.mid'
```

**用 `learner_key` 而不是 `user_id` 是刻意的。** 即使有人拿到了存储桶的列表权限，
也无法直接从路径推断出这些文件属于哪个真实用户。

**谁读：** API（finalize 时校验哈希）、worker（对齐时读取）。

**注意本地开发路径不同。** CLI 的 `align` 子命令用的是扁平结构：

```python
for kind, path in [('reference', args.reference), ('performance', args.performance)]:
    store.put(f'midi/{attempt}/{kind}.mid', path.read_bytes())
```

少一层 `learner_key`，因为本地没有用户概念（`learner_key='synthetic-local'`）。

### `matching/{run_id}/matches.json`

**谁写：** worker，通过 `stage_sink` 回调。

```python
result = align(files['reference'], files['performance'], attempt_id,
               stage_sink=lambda run, data: store.json(f'matching/{run}/matches.json', data))
```

在 `align()` 内部，匹配一完成就立刻落盘：

```python
if stage_sink: stage_sink(run_id, dict(
    attempt_id=attempt_id, score_version=score_hash, performance_hash=performance_hash,
    config=config.identity, matcher_version=matcher_version, raw_matches=raw_records))
```

**为什么要单独存这个：** 后续的偏移校正、判定逻辑都可能出 bug 或需要调整。
有了这份中间产物，你可以在不重跑 parangonar（最慢的一步）的情况下调试下游逻辑。

**谁读：** 目前没有程序读它，纯粹是人工调试资产。

### `alignment/{run_id}/result.json`

**这是整个系统最重要的产物。**

**谁写：** worker。

```python
result_key = f"alignment/{result['alignment_run_id']}/result.json"
store.json(result_key, result)
```

**路径就是内容的哈希**（第 8 章讲的 run ID），这带来几个性质：

- 同样的输入永远写到同一个路径，天然去重
- 不同版本的算法产生不同路径，历史结果永不被覆盖
- 你可以从路径反查「这是哪次运行的结果」

`store.json` 用的是规范化序列化：

```python
def json(self, key, data):
    return self.put(key, json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',',':')).encode())
```

`sort_keys=True` 保证字段顺序确定，`allow_nan=False` 禁止 NaN/Infinity
（它们不是合法 JSON，会让下游解析器行为不一致），
`separators=(',',':')` 去掉多余空格。这些都是为了让**相同数据产生完全相同的字节**。

**谁读：** pipeline 的 `catch_up()` 扫描整个 `alignment/` 前缀；
API 的 `/recommendations` 端点读取用户最近 3 次的结果；
`export_training` 导出训练集时也读它。

### `attempt-results/{attempt_id}/manifest.json`

**谁写：** worker。内容极简：

```json
{"result_key": "alignment/a91f…/result.json"}
```

**为什么需要它：** `alignment/` 是按 run ID 组织的，而 run ID 要靠算法输入推导。
如果你手上只有一个 `attempt_id`，没有这个 manifest 就得重新计算 run ID
（需要知道两个哈希和当时的配置版本）。manifest 提供了一个直接的指针。

worker 开头的查找逻辑就是先看 manifest：

```python
marker_key = f'attempt-results/{attempt_id}/manifest.json'
try:
    marker = json.loads(store.read(marker_key)); result_key = marker['result_key']
    result = json.loads(store.read(result_key))
except (FileNotFoundError,): result = None
```

找到就直接复用，跳过整个对齐计算。

**写入顺序很重要：**

```python
store.json(result_key, result); store.json(marker_key, {'result_key': result_key})
```

**先写结果，后写 manifest。** 如果反过来，manifest 指向一个还不存在的文件，
会造成一个悬空指针。当前顺序下最坏情况是「结果写了但 manifest 没写」，
这个情况有专门的恢复分支（第 8.3 节引用过）。

### `events/*.jsonl`

**谁写：** Pub/Sub 的 Cloud Storage 订阅，不经过任何自己的代码。

文件名由 Pub/Sub 生成，形如：

```
events/2026-09-12T00:00:00+00:00_2026-09-12T00:01:00+00:00_a3f2b1c8.jsonl
```

前缀 `events/` 和后缀 `.jsonl` 来自 Terraform 配置，中间是时间窗口和随机串。
每 60 秒一个文件，内容是每行一个 JSON 对象。

**谁读：** Spark 作业（`spark_events.run()`）。

```python
stream = (spark.readStream.format('text').option('maxFilesPerTrigger', 100).load(raw+'/events')
    .select(F.col('value').alias('payload_json'))
    .withColumn('event_id', F.sha2('payload_json', 256)))
```

注意它是按**文本行**读的，每行原样保存为 `payload_json`，
`event_id` 是这行文本的 SHA-256——所以完全相同的行会得到相同的 ID，天然去重。

## 9.3 lake 桶：Delta 表

`LAKE_ROOT = gs://pianokt-pianokt-lake`

```
gs://pianokt-pianokt-lake/
│
├── bronze/
│   ├── alignment_runs/          ← 完整 result.json 的信封
│   ├── score_notes/             ← 标准答案侧的原始匹配记录
│   ├── performance_notes/       ← 演奏侧的原始匹配记录
│   └── practice_events/         ← 行为事件原文（Spark 写）
│
├── silver/
│   ├── midi_alignment_events/   ← 结构化的音符级判定
│   └── practice_events/         ← 通过校验的行为事件（Spark 写）
│
├── gold/
│   ├── fact_midi_alignment/     ← 音符级事实表
│   ├── hand_events/             ← 手级事实表
│   └── fact_practice_attempt/   ← 每次练习一行的汇总
│
├── quality/
│   ├── alignment_runs/          ← 质量门禁拦下的坏数据
│   └── practice_events/         ← 格式非法的行为事件
│
└── ops/
    ├── completed_runs/          ← 已成功入湖的 run 清单
    └── failures/                ← 入湖失败记录
```

每个目录都是一张 **Delta Lake 表**——不是单个文件，而是
「一堆 Parquet 数据文件 + 一个 `_delta_log/` 事务日志目录」。
Delta 提供 ACID 事务、时间旅行和 MERGE 语义。

## 9.4 checkpoint 桶

`CHECKPOINT_ROOT = gs://pianokt-pianokt-checkpoint`

```
gs://pianokt-pianokt-checkpoint/
└── practice-events-v1/
    ├── offsets/
    ├── commits/
    ├── metadata
    └── sources/
```

只有一个东西：Spark Structured Streaming 的检查点。

```python
stream.writeStream.foreachBatch(process) \
    .option('checkpointLocation', checkpoint+'/practice-events-v1') \
    .trigger(availableNow=True).start().awaitTermination()
```

它记录「上次处理到哪个文件了」。下次作业启动时从这里恢复，
只处理新增的文件，不会重复处理已有的。

**路径里的 `-v1` 是一个版本号。** 如果你改了流式作业的逻辑
（比如换了输出表结构），需要改成 `-v2` 来强制从头重新处理。
直接删除检查点也能达到同样效果，但改名保留了回滚的可能。

**这个桶可以随时清空。** 代价是下次作业会重新扫描所有历史文件。
由于下游用的是 MERGE 语义（按 `event_id` 去重），重复处理不会产生重复数据，
只是浪费一些计算时间。

## 9.5 一次完整练习在 GCS 里留下的痕迹

假设 `learner_key = 8c1b…`、`attempt_id = 3f2a…`、`alignment_run_id = a91f…`：

```
gs://pianokt-pianokt-raw/
  midi/8c1b…/3f2a…/performance.mid          ← 浏览器直传，约 2~50 KB
  midi/8c1b…/3f2a…/reference.mid            ← 浏览器直传，约 2~50 KB
  matching/a91f…/matches.json               ← worker 写，约 100 KB~2 MB
  alignment/a91f…/result.json               ← worker 写，约 200 KB~5 MB
  attempt-results/3f2a…/manifest.json       ← worker 写，约 60 字节
  events/…_….jsonl                          ← Pub/Sub 写，包含这次练习的若干行

gs://pianokt-pianokt-lake/
  bronze/alignment_runs/                    ← +1 行（整个 result.json 塞在 payload_json 里）
  bronze/score_notes/                       ← +N 行（N = 标准答案音符数）
  bronze/performance_notes/                 ← +M 行（M = 演奏音符数）
  silver/midi_alignment_events/             ← +K 行（K = 对齐单元数）
  gold/fact_midi_alignment/                 ← +K 行
  gold/hand_events/                         ← +2 组手级事件
  gold/fact_practice_attempt/               ← +1 行
  ops/completed_runs/                       ← +1 行
  bronze/practice_events/                   ← +若干行（行为事件）
  silver/practice_events/                   ← +若干行
```

粗略估算：一次 3 分钟的练习产生约 5~10 MB 的 raw 数据和几千行湖表记录。

## 9.6 `Objects` 类：统一的存储抽象

所有 GCS 访问都经过 `backend/pianokt_backend/storage.py` 里的这个类。
它的设计目标写在文档字符串里：

```python
class Objects:
    """Create-only storage. Retries with identical bytes succeed."""
```

**创建专用（create-only）** 是核心语义：只能新建，不能覆盖。

```python
def put(self, key, data):
    name = self._name(key)
    if self.bucket:
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(name)
        try: blob.upload_from_string(data, if_generation_match=0)
        except PreconditionFailed:
            if blob.download_as_bytes() != data: raise ValueError('Immutable object conflict')
    else:
        p = self.path/name; p.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=p.parent, delete=False) as f:
            tmp = Path(f.name); f.write(data); f.flush(); os.fsync(f.fileno())
        try: os.link(tmp, p)
        except FileExistsError:
            if p.read_bytes() != data: raise ValueError('Immutable object conflict')
        finally: tmp.unlink(missing_ok=True)
    return self.root+'/'+key
```

**GCS 分支：** `if_generation_match=0` 表示「仅当对象不存在时写入」。
已存在时抛 `PreconditionFailed`，这时下载对比字节——
相同就当作成功（幂等重试），不同才报错（真正的冲突）。

**本地分支：** 用「先写临时文件 + `fsync` + `os.link` 硬链接」模拟同样的语义。
`os.link` 在目标已存在时抛 `FileExistsError`，作用等价于 `if_generation_match=0`。
`fsync` 保证数据真的落盘，防止断电丢失。

**路径安全检查：**

```python
def _name(self, key):
    if key.startswith('/') or '..' in key.split('/'): raise ValueError('Unsafe object key')
    return '/'.join(x for x in (getattr(self,'prefix',''), key) if x)
```

拒绝绝对路径和 `..`，防止路径穿越。虽然目前所有 key 都是程序生成的，
但这道防线让未来引入用户可控路径时不会立刻出事。

**本地/云的透明切换：**

```python
def __init__(self, root):
    self.root = root.rstrip('/'); self.bucket = None
    if root.startswith('gs://'):
        from google.cloud import storage
        p = urlparse(root); self.prefix = p.path.strip('/')
        self.bucket = storage.Client().bucket(p.netloc)
    else: self.path = Path(root).resolve()
```

`RAW_ROOT=data/raw` 就走本地文件系统，`RAW_ROOT=gs://xxx` 就走 GCS。
测试和本地开发不需要连云。

---

# 第 10 章 Bronze / Silver / Gold

## 一句话版本

同一份数据在数据湖里存三遍，每一遍加工程度不同。
Bronze 是「原样抄一份，一个字都不改」，Silver 是「拆成规整的表格，坏数据挑出去」，
Gold 是「直接能拿去做报表和训练的成品」。这样做的代价是存储三倍，
好处是任何一层出问题都能从上一层重新生成。

## 10.1 为什么要分三层

一个很自然的疑问：为什么不直接把对齐结果解析成一张干净的表就完了？

因为**加工逻辑会变，而原始数据不能变**。

假设你写了一段代码把 `result.json` 解析成音符表，跑了三个月，
积累了十万行数据。这时发现解析逻辑有个 bug——某个字段一直取错了。
如果只存了解析后的结果，这十万行全废了，而且原始数据可能已经被清理。

分层的做法是：Bronze 层原封不动地存 `result.json` 的全文，
解析逻辑作用在 Bronze → Silver 这一步。发现 bug 就改代码重跑，
Bronze 层不受影响。

三层的职责边界：

| 层 | 原则 | 允许的操作 |
|---|---|---|
| **Bronze** | 忠实记录 | 只加信封字段（来源、ID），正文一个字节不改 |
| **Silver** | 结构化与清洗 | 拆字段、定类型、剔除坏数据 |
| **Gold** | 面向消费 | 聚合、连接、按业务语义组织 |

额外还有两层不属于主线：

| 层 | 用途 |
|---|---|
| **quality** | 被质量门禁拦下的坏数据，供人工排查 |
| **ops** | 流程自身的元数据（哪些跑完了、哪些失败了） |

## 10.2 两条独立的入湖路径

`pianokt-pipeline` 这个 Job 实际上跑了两个互不相干的作业：

```python
if args.command == 'pipeline' and os.getenv('ENABLE_SPARK_EVENTS') == '1':
    from .pipeline.spark_events import run
    run(os.environ['RAW_ROOT'], os.environ['LAKE_ROOT'], os.environ['CHECKPOINT_ROOT'])
print(json.dumps({'published_runs': catch_up(store, tables, getattr(args,'replay',False))}))
```

**路径一（Spark）：** `events/*.jsonl` → `bronze/silver/quality/practice_events`
**路径二（Python/Delta-rs）：** `alignment/*/result.json` → 其余所有表

两条路径用的技术栈都不一样：前者是 PySpark + Delta Spark，
后者是 `deltalake`（Rust 实现的 delta-rs）+ PyArrow。
这也是为什么 pipeline 用的是单独的 `spark_image`（多装了 JVM 和 Spark）。

## 10.3 并发保护

两条路径都会写 Delta 表，必须防止两个 pipeline 实例同时跑：

```python
from .online import connect
with (connect() if os.getenv('DATABASE_URL') else nullcontext(None)) as lock:
    if lock and not lock.execute("select pg_try_advisory_lock(hashtext('pianokt-lakehouse-v1')) as acquired").fetchone()['acquired']:
        raise RuntimeError('Pipeline writer already running')
    try:
        ...
    finally:
        if lock: lock.execute("select pg_advisory_unlock(hashtext('pianokt-lakehouse-v1'))")
```

用的是 PostgreSQL 的**咨询锁**（advisory lock）。它不锁任何表，
只是一个全局命名的互斥量。`pg_try_advisory_lock` 是非阻塞的——
拿不到就立刻返回 false，然后作业直接失败退出，而不是排队等待。

这样设计是因为定时任务重叠执行通常意味着上一次跑太久了，
排队只会让情况更糟。直接失败并告警更合理。

## 10.4 路径二：对齐结果入湖

### 增量控制

```python
def catch_up(objects, tables, replay=False):
    completed = set() if replay else {r['event_id'] for r in tables.rows('ops/completed_runs')}
    failures = []; count = 0
    for key in objects.keys('alignment'):
        if not key.endswith('/result.json'): continue
        try:
            result = json.loads(objects.read(key))
            if result['alignment_run_id'] in completed: continue
            publish_alignment(result, tables, key); count += 1
        except Exception as exc:
            failures.append(key)
            tables.merge('ops/failures', [dict(event_id=stable_id(key),
                event_type='materialization.failed', source_key=key,
                payload_json=json.dumps({'error_type': type(exc).__name__}))], ENVELOPE_SCHEMA, 'event_id')
    if failures: raise RuntimeError(f'{len(failures)} failed runs; see ops/failures')
    return count
```

它**扫描整个 `alignment/` 前缀**，用 `ops/completed_runs` 做已处理集合。
这个设计的好处是不依赖任何外部状态——即使数据库丢了，
只要 GCS 还在，重跑一次就能把湖重建出来。

`replay=True` 时清空已完成集合，强制全量重跑。

单个 run 失败不会中断整批：错误被记录到 `ops/failures`，
循环继续处理后面的。全部处理完之后才抛异常，
这样一个坏数据不会阻塞其他好数据入湖。

注意 `payload_json` 里只记了 `error_type`（异常类名），
**没有记完整的异常消息**。这是为了避免把用户数据或内部路径泄露进湖表。

### `publish_alignment`：一次 run 的完整物化

```python
def publish_alignment(result, tables, source_key):
    run = result['alignment_run_id']
    meta = {k: result[k] for k in ['attempt_id','learner_key','song_id']}
    envelope = dict(event_id=run, event_type='alignment.completed', source_key=source_key,
                    payload_json=json.dumps(result, allow_nan=False))
    tables.merge('bronze/alignment_runs', [envelope], ENVELOPE_SCHEMA, 'event_id')
```

**Bronze 第一张表：** 整个 `result.json` 原封不动塞进 `payload_json`，
外面套一个四字段的信封。

```python
ENVELOPE_SCHEMA = pa.schema([(k, pa.string()) for k in
    ['event_id','event_type','source_key','payload_json']])
```

`source_key` 记录了这行数据来自 GCS 的哪个对象——**血缘信息**。
任何时候都能顺着它回到原始文件。

```python
    for side, table in [('ref','score_notes'), ('performance','performance_notes')]:
        unique = {}
        for raw in result['raw_matches']:
            nid = raw.get(side+'_id')
            if nid is None: continue
            key = stable_id(result['score_version'] if side=='ref' else result['attempt_id'], nid)
            unique[key] = dict(event_id=key, event_type=table, source_key=source_key,
                               payload_json=json.dumps({k:v for k,v in raw.items() if k.startswith(side+'_')}))
        tables.merge('bronze/'+table, list(unique.values()), ENVELOPE_SCHEMA, 'event_id')
```

**Bronze 另外两张表：** 把 parangonar 的原始匹配记录拆成两侧。

去重键的设计很讲究：

- 标准答案侧用 `stable_id(score_version, ref_id)` —— **同一份乐谱的同一个音符，
  无论被多少个学生弹过多少次，在 `bronze/score_notes` 里只有一行**。
- 演奏侧用 `stable_id(attempt_id, performance_id)` —— 每次演奏的每个音符独立一行。

这大幅减少了乐谱侧的冗余。

### 质量门禁

在写 Silver 之前有一道检查：

```python
    errors = []
    notes = []
    for r in result['notes']:
        label = r['alignment_type']
        if label not in ('match','deletion','insertion') or r['reject_reason'] not in range(7):
            errors.append('invalid_label')
        if label in ('match','deletion') and r.get('ref_id') is None:
            errors.append('missing_reference')
        if label in ('match','insertion') and r.get('performance_id') is None:
            errors.append('missing_performance')
        notes.append(dict(r, **meta, payload_json=json.dumps(r, allow_nan=False)))
    hand_rows = []
    for hand, a in result['events_data']['data'].items():
        if len({len(v) for v in a.values()}) != 1:
            errors.append('hand_array_length'); continue
        for i in range(len(a['reject_reason'])):
            hand_rows.append(dict(meta, event_id=stable_id(run, hand, i),
                                  alignment_run_id=run, hand=hand,
                                  **{k: v[i] for k, v in a.items()}))
    if errors:
        tables.merge('quality/alignment_runs',
                     [dict(envelope, payload_json=json.dumps({'errors': errors}))],
                     ENVELOPE_SCHEMA, 'event_id')
        raise ValueError('Alignment quality gate failed')
```

四条不变量：

| 检查 | 含义 |
|---|---|
| `invalid_label` | `alignment_type` 必须是三种之一，`reject_reason` 必须在 0~6 |
| `missing_reference` | match 和 deletion 必须有 `ref_id` |
| `missing_performance` | match 和 insertion 必须有 `performance_id` |
| `hand_array_length` | 同一只手的所有列式数组长度必须相同 |

最后一条尤其重要。`events_data` 是列式存储：

```json
{"reject_reason": [0,0,3], "onset_time": [0,500,1000], "pitches": [[60],[64],[67]]}
```

如果 `reject_reason` 有 3 个元素而 `onset_time` 只有 2 个，
按下标组装出来的行就是错位的——第 3 行会取到不存在的数据或抛异常。
显式检查所有数组等长，比事后发现数据错乱好得多。

**门禁失败的行为：** 把错误清单写进 `quality/alignment_runs`，然后**抛异常**。
这条 run 不会进入 Silver 和 Gold。上层的 `catch_up` 捕获这个异常，
记进 `ops/failures` 并继续处理下一条。

注意 `quality` 表里存的是错误清单，不是原始数据（`payload_json` 被替换掉了）。
原始数据在 `bronze/alignment_runs` 里已经有一份了。

### Silver 与 Gold

```python
    tables.merge('silver/midi_alignment_events', notes, NOTE_SCHEMA, 'alignment_event_id')
    tables.merge('gold/fact_midi_alignment', notes, NOTE_SCHEMA, 'alignment_event_id')
    tables.merge('gold/hand_events', hand_rows, HAND_SCHEMA, 'event_id')
    summary = dict(meta, alignment_run_id=run, score_version=result['score_version'],
                   alignment_version=result['config']['version'], **result['summary'])
    tables.merge('gold/fact_practice_attempt', [summary], SUMMARY_SCHEMA, 'alignment_run_id')
    # Commit manifest last; a Delta transaction is per table, not across this function.
    tables.merge('ops/completed_runs', [envelope], ENVELOPE_SCHEMA, 'event_id')
```

**注意 `silver/midi_alignment_events` 和 `gold/fact_midi_alignment` 写的是同一份 `notes`。**
当前阶段两者内容完全一致，Gold 只是预留了未来做进一步加工的位置。
这是一个有意的简化，不是 bug。

### 最后一行的注释值得展开

```python
# Commit manifest last; a Delta transaction is per table, not across this function.
```

Delta Lake 的事务粒度是**单表**。这个函数写了 8 张表，
它们是 8 个独立的事务，没有跨表的原子性。

如果进程在写完 `gold/fact_practice_attempt` 之后、写 `ops/completed_runs` 之前崩溃，
会发生什么？下次运行时 `completed` 集合里没有这个 run，于是重新处理一遍。
所有的 `merge` 都是按主键 upsert 的，重复处理只会覆盖成相同的值。

**把 `ops/completed_runs` 放在最后写，是把它当成一个「提交标记」。**
只有它写成功了，才代表整条流水线真的完成。这是在没有分布式事务的情况下
实现最终一致性的标准手法。

## 10.5 四个 Schema

```python
ENVELOPE_SCHEMA = pa.schema([(k, pa.string()) for k in
    ['event_id','event_type','source_key','payload_json']])

NOTE_SCHEMA = pa.schema(
    [(k, pa.string()) for k in ['alignment_event_id','alignment_run_id','attempt_id','learner_key',
                                'song_id','score_version','performance_hash','alignment_version','alignment_type']]
  + [(k, pa.float64()) for k in ['ref_pitch','performance_pitch','ref_onset_sec','performance_onset_sec',
                                 'performance_onset_sec_global_aligned','timing_deviation_global_aligned']]
  + [('reject_reason', pa.int32()), ('is_correct', pa.bool_()),
     ('within_observed_range', pa.bool_()), ('timing_labels_valid', pa.bool_()),
     ('payload_json', pa.string())])

HAND_SCHEMA = pa.schema(
    [(k, pa.string()) for k in ['event_id','alignment_run_id','attempt_id','learner_key','song_id','hand']]
  + [('reject_reason', pa.int32()), ('timing_offset', pa.float64()), ('pitch_offset', pa.float64()),
     ('duration', pa.float64()), ('onset_time', pa.float64()), ('pitches', pa.list_(pa.int32()))])

SUMMARY_SCHEMA = pa.schema(
    [(k, pa.string()) for k in ['alignment_run_id','attempt_id','learner_key','song_id',
                                'score_version','alignment_version']]
  + [(k, pa.int64()) for k in ['expected_notes','correct_notes','extra_notes','missing_notes']]
  + [('accuracy', pa.float64()), ('timing_labels_valid', pa.bool_())])
```

几点观察：

**音高用 `float64` 而不是整数。** 因为 deletion 行的 `performance_pitch` 是 NULL，
而 PyArrow 的整数列表达 NULL 需要额外处理。用浮点更简单，代价是语义上略显奇怪。

**`payload_json` 在 NOTE_SCHEMA 里也保留了。** 即使已经拆出了结构化字段，
原始 JSON 仍然带着。这样将来需要某个没被拆出来的字段时，不用回 Bronze 层。

**`pitches` 是 `list(int32)`。** 一个手级事件可能对应一个和弦的多个音。

**`accuracy` 可以为 NULL。** 对应第 8 章说的「没弹和弹错是两回事」。

## 10.6 `Tables.merge`：写入的实现

```python
class Tables:
    def __init__(self, root): self.root = root.rstrip('/')
    def merge(self, name, rows, schema, key):
        if not rows: return
        source = pa.Table.from_pylist(rows, schema=schema)
        if len(set(source[key].to_pylist())) != len(rows): raise ValueError('Duplicate source keys')
        path = self.root+'/'+name
        try: table = DeltaTable(path)
        except TableNotFoundError:
            write_deltalake(path, source, mode='error'); return
        table.merge(source, predicate=f't.{key}=s.{key}', source_alias='s', target_alias='t') \
             .when_matched_update_all().when_not_matched_insert_all().execute()
    def rows(self, name):
        try: return DeltaTable(self.root+'/'+name).to_pyarrow_table().to_pylist()
        except TableNotFoundError: return []
```

**主键唯一性检查在写入之前。** `if len(set(...)) != len(rows)` 拦住了
「一批数据里有重复主键」的情况。这很重要，因为 MERGE 在源端有重复键时
行为是未定义的（可能报错，可能随机取一条）。

**表不存在时用 `mode='error'` 创建。** 这个模式在表已存在时会报错，
配合外层的 `except TableNotFoundError` 形成一个安全的「首次创建」路径。

**`when_matched_update_all().when_not_matched_insert_all()`** 就是标准的 upsert：
主键已存在就整行更新，不存在就插入。这让整个流水线**天然幂等**——
重跑多少次结果都一样。

## 10.7 路径一：Spark 处理行为事件

```python
def run(raw, lake, checkpoint):
    builder = (SparkSession.builder.appName('pianokt-events')
        .config('spark.sql.shuffle.partitions','2')
        .config('spark.hadoop.fs.gs.impl','com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem')
        .config('spark.hadoop.fs.AbstractFileSystem.gs.impl','com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS')
        .config('spark.sql.extensions','io.delta.sql.DeltaSparkSessionExtension')
        .config('spark.sql.catalog.spark_catalog','org.apache.spark.sql.delta.catalog.DeltaCatalog'))
    jars = os.getenv('PIANOKT_SPARK_JARS')
    spark = (builder.config('spark.jars', jars) if jars else configure_spark_with_delta_pip(builder)).getOrCreate()
```

`shuffle.partitions = 2` 是针对小数据量的优化。Spark 默认 200 个分区，
在数据只有几千行时会产生 200 个几乎空的文件，严重拖慢速度。

`PIANOKT_SPARK_JARS` 在 Dockerfile.spark 里设置：

```dockerfile
ENV PYSPARK_SUBMIT_ARGS="--master local[2] pyspark-shell" \
    PIANOKT_SPARK_JARS="/opt/jars/delta-spark.jar,/opt/jars/delta-storage.jar,/opt/jars/gcs.jar"
```

预先把 JAR 打进镜像，避免运行时从 Maven 下载（Cloud Run 里下载慢且不稳定）。
`local[2]` 表示单机两个线程——这不是一个分布式集群，就是一个单容器的 Spark。

### 空目录检查

```python
path = spark._jvm.org.apache.hadoop.fs.Path(raw+'/events')
if not path.getFileSystem(spark._jsc.hadoopConfiguration()).exists(path): spark.stop(); return
```

`events/` 目录不存在时（还没有任何事件被归档）直接退出，
否则 Spark 会抛一个不太友好的异常。

### 流式读取

```python
stream = (spark.readStream.format('text').option('maxFilesPerTrigger', 100).load(raw+'/events')
    .select(F.col('value').alias('payload_json'))
    .withColumn('event_id', F.sha2('payload_json', 256)))
```

**按纯文本读，一行一条记录。** 不做 JSON 解析——那是 Silver 层的事。

`event_id` 是整行文本的 SHA-256。这意味着**完全相同的行只会有一个 ID**，
天然去重。即使 Pub/Sub 因为至少一次投递把同一条消息存了两遍，
湖里也只会有一行。

`maxFilesPerTrigger = 100` 限制每批最多处理 100 个文件，防止首次运行时
一次加载海量历史数据导致内存溢出。

### 分层写入

```python
def upsert(df, path):
    if df.isEmpty(): return
    if DeltaTable.isDeltaTable(spark, path):
        DeltaTable.forPath(spark, path).alias('t').merge(df.alias('s'),'t.event_id=s.event_id') \
            .whenNotMatchedInsertAll().execute()
    else: df.write.format('delta').save(path)

def process(batch, batch_id):
    batch = batch.dropDuplicates(['event_id']).persist()
    try:
        upsert(batch, lake+'/bronze/practice_events')
        parsed = batch.withColumn('parsed', F.from_json('payload_json', schema))
        valid = F.col('parsed.event_id').isNotNull() & F.col('parsed.event_type').isNotNull() \
              & (F.length('parsed.event_id') > 0) & (F.length('parsed.event_type') > 0)
        upsert(parsed.filter(~valid).drop('parsed'), lake+'/quality/practice_events')
        upsert(parsed.filter(valid).select(F.col('parsed.event_id').alias('event_id'), 'payload_json')
                     .dropDuplicates(['event_id']), lake+'/silver/practice_events')
    finally: batch.unpersist()
```

流程：

1. **批内去重**（`dropDuplicates`），然后 `persist()` 缓存——
   下面要用三次，不缓存会重复计算。
2. **Bronze：** 原始文本原样写入。
3. **解析：** `from_json` 只提取两个字段（`event_id`、`event_type`），
   不做完整解析。
4. **Quality：** 解析失败或字段为空的行进这里。
5. **Silver：** 通过校验的行，用**消息自己声明的 `event_id`** 作为主键
   （而不是文本哈希），再去重一次。

第 5 步的主键切换很关键。Bronze 用文本哈希去重，能挡住「完全相同的行」；
Silver 用业务 ID 去重，能挡住「同一个事件的不同序列化形式」
（比如 JSON 字段顺序不同但内容相同）。两层各挡一类问题。

**注意 Silver 只用了 `whenNotMatchedInsertAll()`，没有 update 分支。**
行为事件是不可变事实，已存在就不该被改写。

### 触发方式

```python
stream.writeStream.foreachBatch(process) \
    .option('checkpointLocation', checkpoint+'/practice-events-v1') \
    .trigger(availableNow=True).start().awaitTermination()
```

`availableNow=True` 是一种**批处理式的流**：
处理完当前所有可用数据就自动停止，而不是常驻等待新数据。

这非常适合 Cloud Run Job 的模型——任务跑完就退出，按秒计费。
常驻流式作业需要一直开着实例，成本高得多。

## 10.8 怎么查这些表

Delta 表可以用任何支持 Delta 的工具读。最轻量的是 Python：

```python
from deltalake import DeltaTable
import pandas as pd

dt = DeltaTable('gs://pianokt-pianokt-lake/gold/fact_practice_attempt')
df = dt.to_pandas()
print(df[['song_id','accuracy','expected_notes','correct_notes']].head())
```

看某个学生的进步曲线：

```python
notes = DeltaTable('gs://pianokt-pianokt-lake/gold/fact_midi_alignment').to_pandas()
mine = notes[(notes.learner_key == '8c1b…') & notes.within_observed_range & notes.timing_labels_valid]
mine.groupby('attempt_id').agg(accuracy=('is_correct','mean'), notes=('is_correct','size'))
```

查历史版本（时间旅行）：

```python
dt = DeltaTable('gs://…/gold/fact_practice_attempt', version=5)
# 或按时间
dt.load_as_version('2026-09-01T00:00:00Z')
```

---

# 第 11 章 权限与身份

## 一句话版本

系统里有很多个「身份」，每个身份只被授予它干活所必需的最小权限。
这样做的意义是：任何一个环节被攻破，攻击者能拿到的东西都很有限。
这一章把每个身份能做什么、不能做什么列清楚。

## 11.1 身份全景

| 身份 | 类型 | 谁在用 |
|---|---|---|
| `anon` | Supabase 数据库角色 | 未登录的浏览器 |
| `authenticated` | Supabase 数据库角色 | 已登录的浏览器 |
| `pianokt_backend` | PostgreSQL 角色 | 后端所有服务 |
| `pianokt-api` | GCP 服务账号 | api Cloud Run 服务 |
| `pianokt-worker` | GCP 服务账号 | worker Cloud Run 服务 |
| `pianokt-relay` | GCP 服务账号 | relay Cloud Run 任务 |
| `pianokt-pipeline` | GCP 服务账号 | pipeline Cloud Run 任务 |
| `pianokt-scheduler` | GCP 服务账号 | Cloud Scheduler |
| `pianokt-push` | GCP 服务账号 | Pub/Sub 推送时的身份 |
| Pub/Sub 服务身份 | GCP 托管身份 | Pub/Sub 服务本身 |

## 11.2 浏览器侧：RLS 是唯一的防线

浏览器持有 anon key，它能直接连 Supabase 的 REST 接口。
这意味着**用户可以构造任意 SQL 查询**（通过 PostgREST 的语法）。
唯一阻止他读别人数据的，是 Row Level Security 策略。

### 完全不可访问的表

```sql
revoke all on public.piano_outbox, public.piano_learner_keys,
  public.piano_recommendation_feedback from anon, authenticated;
```

这三张表对浏览器完全关闭。`piano_learner_keys` 尤其重要——
它是伪名到真实身份的映射，泄露就等于伪名化失效。

### 只读自己数据的表

```sql
create policy attempts_read on public.piano_attempts
  for select to authenticated using(user_id = auth.uid());
create policy "Users can read own challenge_recordings" on public.challenge_recordings
  for select using (auth.uid() = user_id);
create policy "Users can read own play_events_raw" on public.play_events_raw
  for select using (auth.uid() = user_id);
create policy "Users can read own user_play_logs" on public.user_play_logs
  for select using (auth.uid() = user_id);
```

`auth.uid()` 是 Supabase 提供的函数，从 JWT 里解出用户 ID。
它由 Postgres 在服务端计算，**客户端无法伪造**。

### 可写的表

只有两处：

```sql
create policy "Users can insert own play_events_raw" on public.play_events_raw
  for insert with check (auth.uid() = user_id);
create policy "Users can insert own challenge_recordings" on public.challenge_recordings
  for insert with check (auth.uid() = user_id);
create policy preferences_own on public.piano_preferences
  for all to authenticated using(user_id=auth.uid()) with check(user_id=auth.uid());
```

注意**没有任何表允许 update 或 delete**（偏好表除外）。
所有事实数据对用户是只增不改的。

### `user_play_logs` 没有 insert 策略

这张表只能由 `security definer` 函数写入：

```sql
create or replace function public.upsert_user_play_log(p_session_id uuid)
returns void language plpgsql security definer set search_path = public
```

`security definer` 让函数以**创建者**（通常是 postgres）的身份运行，
从而绕过 RLS。这是受控的提权——函数内部自己做了权限检查：

```sql
v_user_id := auth.uid();
if v_user_id is null then return; end if;
...
where e.session_id = p_session_id and e.user_id = v_user_id;
```

即使用户传了别人的 `session_id`，`and e.user_id = v_user_id` 也会让查询结果为空，
`v_events_count = 0`，函数直接返回。

`set search_path = public` 是 `security definer` 函数的**必备防护**。
没有它，攻击者可以创建一个同名的 schema 并放进恶意函数，
劫持函数内部的调用。

## 11.3 后端侧：专用数据库角色

后端不用 postgres 超级用户，也不用 service-role key。
`supabase/backend_role.sql` 定义了一个专用角色：

```sql
create role pianokt_backend nologin;
grant usage on schema public to pianokt_backend;
grant select,insert,update on public.piano_attempts, public.piano_learner_keys, public.piano_outbox,
 public.piano_recommendations, public.piano_recommendation_feedback, public.challenge_recordings to pianokt_backend;
grant select on public.piano_song_catalog, public.piano_preferences to pianokt_backend;
create policy backend_attempts on public.piano_attempts to pianokt_backend using(true) with check(true);
create policy backend_keys on public.piano_learner_keys to pianokt_backend using(true) with check(true);
create policy backend_outbox on public.piano_outbox to pianokt_backend using(true) with check(true);
create policy backend_recommendations on public.piano_recommendations to pianokt_backend using(true) with check(true);
create policy backend_feedback on public.piano_recommendation_feedback to pianokt_backend using(true) with check(true);
create policy backend_catalog on public.piano_song_catalog for select to pianokt_backend using(true);
create policy backend_preferences on public.piano_preferences for select to pianokt_backend using(true);
```

几个关键设计：

**`nologin`。** 这个角色本身不能登录。文件末尾的注释说明了用法：

```sql
-- Create a dedicated LOGIN role with a password via your secret-management workflow,
-- then GRANT pianokt_backend TO that_login. Never grant this role to anon/authenticated.
```

密码属于另一个 LOGIN 角色，通过 Secret Manager 管理。
这样轮换密码不需要动权限定义。

**没有 `BYPASSRLS`，没有超级用户。** 角色通过**显式的宽松策略**
（`using(true) with check(true)`）访问它需要的表，而不是绕过 RLS 机制。
区别在于：宽松策略是逐表授予的，一目了然；`BYPASSRLS` 是全局的，
将来新增一张敏感表会默认对它开放。

**授权列表是白名单。** 注意 `play_events_raw` 和 `user_play_logs`
**不在**授权列表里——后端根本不需要访问行为事件表，
那条链路完全由数据库触发器和 RPC 处理。

**只有 select / insert / update，没有 delete。** 后端不能删任何数据。

## 11.4 GCP 服务账号

六个服务账号一次性声明：

```hcl
resource "google_service_account" "runtime" {
  for_each   = toset(["api", "worker", "relay", "pipeline", "scheduler", "push"])
  account_id = "pianokt-${each.value}"
}
```

### 存储权限矩阵

```hcl
resource "google_storage_bucket_iam_member" "raw_read" {
  for_each = toset(["api", "worker", "pipeline"])
  bucket   = google_storage_bucket.data["raw"].name
  role     = "roles/storage.objectViewer"
  ...
}
resource "google_storage_bucket_iam_member" "raw_create" {
  for_each = toset(["api", "worker"])
  bucket   = google_storage_bucket.data["raw"].name
  role     = "roles/storage.objectCreator"
  ...
}
resource "google_storage_bucket_iam_member" "tables" {
  for_each = toset(["lake", "checkpoint"])
  bucket   = google_storage_bucket.data[each.value].name
  role     = "roles/storage.objectAdmin"
  member   = "serviceAccount:${google_service_account.runtime["pipeline"].email}"
}
```

整理成表：

| 服务账号 | raw 读 | raw 写 | lake | checkpoint |
|---|:---:|:---:|:---:|:---:|
| `pianokt-api` | ✅ | ✅ 仅创建 | ❌ | ❌ |
| `pianokt-worker` | ✅ | ✅ 仅创建 | ❌ | ❌ |
| `pianokt-pipeline` | ✅ | ❌ | ✅ 完全 | ✅ 完全 |
| `pianokt-relay` | ❌ | ❌ | ❌ | ❌ |
| `pianokt-scheduler` | ❌ | ❌ | ❌ | ❌ |
| `pianokt-push` | ❌ | ❌ | ❌ | ❌ |

几个值得注意的点：

**`objectCreator` 而不是 `objectAdmin`。** 前者只能新建对象，
**不能覆盖也不能删除**。这在 IAM 层面强化了第 9 章讲的「创建专用」语义——
即使代码有 bug 想覆盖，云端也会拒绝。

**pipeline 对 raw 只读。** 它读对齐结果和事件归档，但绝不修改原始数据。

**pipeline 对 lake 是 `objectAdmin`（完全权限）。** Delta Lake 的
compaction、vacuum 等维护操作需要删除旧文件，所以这里必须给写和删。

**relay / scheduler / push 完全没有存储权限。** 它们只处理消息和调度，
不碰数据。

### 签名 URL 的特殊授权

```hcl
resource "google_service_account_iam_member" "sign" {
  service_account_id = google_service_account.runtime["api"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.runtime["api"].email}"
}
```

**api 服务账号对自己有 `serviceAccountTokenCreator`。** 这看起来很奇怪，
但它是 Cloud Run 上生成 V4 签名 URL 的标准做法。

原因是签名需要私钥，而 Cloud Run 的运行时身份是通过元数据服务提供的短期令牌，
没有私钥文件。`generate_signed_url` 在指定 `service_account_email` 和
`access_token` 时会改走 IAM Credentials API 的 `signBlob` 接口——
让 IAM 服务代为签名。这个操作需要对目标服务账号有 tokenCreator 权限。

### Secret 访问

```hcl
resource "google_secret_manager_secret_iam_member" "database" {
  for_each  = toset(["api", "worker", "relay", "pipeline"])
  secret_id = var.database_secret_id
  role      = "roles/secretmanager.secretAccessor"
  ...
}
```

四个需要连数据库的身份都能读那个 secret。
`scheduler` 和 `push` 不在列表里——它们不碰数据库。

### Pub/Sub 权限

```hcl
resource "google_pubsub_topic_iam_member" "relay" {
  topic  = google_pubsub_topic.events.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.runtime["relay"].email}"
}
```

**只有 relay 能往主题发消息。** api 和 worker 都不能——
它们只往 `piano_outbox` 表写。这在权限层面强制了 Outbox 模式：
即使有人在 api 里写了直接 publish 的代码，也会因为权限不足而失败。

### Cloud Run 调用权限

```hcl
resource "google_cloud_run_v2_service_iam_member" "api" {
  name = …["api"].name; role = "roles/run.invoker"; member = "allUsers"
}
resource "google_cloud_run_v2_service_iam_member" "worker" {
  name = …["worker"].name; role = "roles/run.invoker"
  member = "serviceAccount:${google_service_account.runtime["push"].email}"
}
resource "google_cloud_run_v2_job_iam_member" "scheduler" {
  for_each = google_cloud_run_v2_job.job
  name = each.value.name; role = "roles/run.invoker"
  member = "serviceAccount:${google_service_account.runtime["scheduler"].email}"
}
```

| 服务 | 谁能调用 |
|---|---|
| `pianokt-api` | 所有人（公开，靠 JWT 鉴权） |
| `pianokt-worker` | 只有 `pianokt-push` |
| `pianokt-relay` / `pianokt-pipeline` | 只有 `pianokt-scheduler` |

worker 那条是**不可妥协的安全边界**，第 7 章已详述。

### Pub/Sub 服务身份

```hcl
resource "google_project_service_identity" "pubsub" {
  provider = google-beta
  service  = "pubsub.googleapis.com"
}
```

这是 GCP 为 Pub/Sub 服务自身创建的托管身份，形如
`service-{project_number}@gcp-sa-pubsub.iam.gserviceaccount.com`。
它需要三组权限：

```hcl
# 1. 写事件归档
google_storage_bucket_iam_member.archive_create   → objectCreator on raw
google_storage_bucket_iam_member.archive_bucket   → legacyBucketReader on raw
# 2. 生成 OIDC 令牌冒充 push 账号
google_service_account_iam_member.push_token      → tokenCreator on pianokt-push
# 3. 死信机制
google_pubsub_topic_iam_member.dead_publish       → publisher on dead-letter
google_pubsub_subscription_iam_member.dead_subscribe → subscriber on worker 订阅
```

## 11.5 邮箱白名单

```sql
create table if not exists public.allowed_emails (email text primary key);
alter table public.allowed_emails enable row level security;
-- 没有任何 select/insert/update/delete 策略：只有 service role / Dashboard 能管理
```

**表本身对任何人都不可读。** 用户无法枚举白名单里有谁。
访问只能通过一个 `security definer` 函数：

```sql
create or replace function public.check_user_allowed()
returns boolean language plpgsql security definer set search_path = public, auth
as $$
declare user_email text; whitelist_count int;
begin
  select coalesce(u.email, u.raw_user_meta_data->>'email') into user_email
  from auth.users u where u.id = auth.uid();
  if user_email is null then return false; end if;
  select count(*) into whitelist_count from public.allowed_emails;
  if whitelist_count = 0 then return true; end if;
  return exists (select 1 from public.allowed_emails a
                 where lower(trim(a.email)) = lower(trim(user_email)));
end;
$$;
grant execute on function public.check_user_allowed() to authenticated;
grant execute on function public.check_user_allowed() to anon;
```

三个行为要点：

**空白名单等于全部放行。** `if whitelist_count = 0 then return true`。
这让新部署的实例不会把所有人锁在外面。

**函数只返回布尔值。** 调用者只能知道「自己是否被允许」，
无法探测别人的状态（因为邮箱是从 `auth.uid()` 反查的，不是参数）。

**大小写和空格不敏感。** `lower(trim(...))` 两侧都做了归一化。

### 前后端执行强度不一致

这是一个真实存在的不对称，前面章节提到过，这里正式记录：

```ts
// 前端：fail-open
const { data, error: rpcErr } = await supabase.rpc('check_user_allowed')
if (rpcErr) return          // ← RPC 失败就当通过
if (data === false) { await supabase.auth.signOut(); ... }
```

```python
# 后端：fail-closed
if allowed.status_code != 200: raise HTTPException(503, 'Access policy unavailable')
if allowed.json() is not True: raise HTTPException(403, 'Account is not on the access list')
```

后端是正确的做法。前端的 fail-open 意味着**白名单在客户端从来不是强约束**——
它只是一个体验优化（提前告知用户并登出），真正的执行在后端。

如果白名单是安全需求而不只是体验需求，前端那行应该改成失败即拒绝。

---

# 第 12 章 故障排查手册

## 一句话版本

这一章把已知的每一个坑写成「你看到什么现象 → 真正的原因是什么 → 怎么验证 → 怎么修」。
大部分故障的共同特征是**报错信息指向的地方不是真正出问题的地方**。

## 12.1 快速体检脚本

怀疑系统有问题时，先跑一遍这些命令定位大方向。

```bash
PROJECT=pianokt
REGION=us-central1
SUPA=https://nygtnkuisddcpbgitwhr.supabase.co
KEY=<你的 anon key>

echo "=== 1. Cloud Run 服务 ==="
gcloud run services list --project $PROJECT --region $REGION \
  --format="table(metadata.name,status.url,status.conditions[0].status)"

echo "=== 2. API 健康检查 ==="
API=$(gcloud run services describe pianokt-api --region $REGION --project $PROJECT --format='value(status.url)')
curl -s "$API/health"

echo "=== 3. CORS 预检 ==="
curl -s -i -X OPTIONS "$API/attempts" \
  -H "Origin: https://pianokt-anu.vercel.app" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: authorization,content-type" | grep -i access-control

echo "=== 4. 关键 RPC 是否存在 ==="
for f in check_user_allowed get_leaderboard; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SUPA/rest/v1/rpc/$f" \
    -H "apikey: $KEY" -H 'Content-Type: application/json' -d '{}')
  printf '  %-24s %s\n' "$f" "$code"
done

echo "=== 5. 关键表是否存在 ==="
for t in allowed_emails profiles challenge_recordings play_events_raw user_play_logs piano_attempts piano_outbox; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$SUPA/rest/v1/$t?select=*&limit=0" -H "apikey: $KEY")
  printf '  %-24s %s\n' "$t" "$code"
done

echo "=== 6. 定时任务状态 ==="
gcloud scheduler jobs list --location $REGION --project $PROJECT \
  --format="table(name,schedule,state)"

echo "=== 7. GCS 桶 CORS ==="
gcloud storage buckets describe gs://$PROJECT-pianokt-raw --format="default(cors_config)"
```

状态码含义：`200` 正常；`401` 权限被 RLS 拦住（表存在）；`404` 对象不存在（**缺 migration**）。

数据库侧的体检 SQL：

```sql
-- outbox 有没有在流动
select event_type, count(*) as total, count(published_at) as published
from piano_outbox group by 1;

-- 行为事件的动作分布（注意 event_type 都是 practice.event）
select payload->>'action' as action, count(*)
from piano_outbox where event_type='practice.event' group by 1 order by 2 desc;

-- attempt 卡在哪个状态
select status, count(*), max(updated_at) from piano_attempts group by 1;

-- 有原始事件但没聚合出学习记录的 session
select e.session_id, count(*) as events,
       bool_or(e.event_type in ('finished','exited','failed')) as has_terminal
from play_events_raw e
left join user_play_logs l on l.session_id = e.session_id
where l.session_id is null
group by 1 order by 1;
```

## 12.2 前端：`PianoKT backend is not configured`

**报错位置：** `src/features/challenge-history/gcs.ts`

```ts
if (!apiRoot || !supabase) throw new Error('PianoKT backend is not configured')
```

**关键点：这句话有两个触发条件。** 很多人只检查 API URL，
但 `supabase` 为 null 时报的是同一句话。

**排查顺序：**

1. 检查 Vercel 环境变量里 `VITE_PIANOKT_API_URL`、`VITE_SUPABASE_URL`、
   `VITE_SUPABASE_ANON_KEY` 三个是否都存在，且勾选了你正在访问的那个 Environment
   （Production / Preview / Development 是分开的）。

2. **改完必须重新部署。** Vite 在构建时把变量写死进包里，
   只刷新页面不会生效。

3. 验证变量是否真的进了包：打开线上页面的 DevTools，
   Sources 面板全局搜索（Cmd+Opt+F）你的 Cloud Run 域名片段（如 `run.app`）。
   搜得到说明 API URL 进去了，问题在 Supabase 那两个；搜不到就是 API URL 没进构建。

4. 本地开发时检查 `.env.local` 是不是还是占位符，改完要重启 dev server。

**API URL 填什么：** `pianokt-api` 的 Cloud Run 地址，只填根路径。
**不要填 worker 的地址**——worker 是私有的，会返回 403，
而 403 响应没有 CORS 头，浏览器会报成 `Failed to fetch`。

## 12.3 前端：`Save failed: failed to fetch`

`TypeError: Failed to fetch` 是浏览器的通用网络错误，可能来自三个位置：

```ts
const urls = await backendRequest<…>('/attempts', {…})        // ① Cloud Run
await fetch(url, { method:'PUT', … })                          // ② storage.googleapis.com
const result = await backendRequest<…>(`/attempts/${id}/finalize`, {})  // ③ Cloud Run
```

三者的报错完全一样，必须打开 DevTools 的 Network 面板看是哪一个请求红了。

**最常见的原因是 CORS。** `frontend_origin` 这个 Terraform 变量同时决定
API 的 CORS 白名单和 raw 桶的 CORS 规则，任何一处不匹配都会挂。

典型场景：部署 Terraform 时 `frontend_origin` 填了占位值或 localhost，
后来才把 Vercel 域名写进 tfvars 但没有重新 apply。

**验证：**

```bash
API=$(gcloud run services describe pianokt-api --region us-central1 --project pianokt --format='value(status.url)')
curl -i -X OPTIONS "$API/attempts" \
  -H "Origin: https://pianokt-anu.vercel.app" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: authorization,content-type"
```

响应头里没有 `access-control-allow-origin: https://pianokt-anu.vercel.app` 就是坐实了。

再看桶的 CORS：

```bash
gcloud storage buckets describe gs://pianokt-pianokt-raw --format="default(cors_config)"
```

看当前烤进 Cloud Run 的值：

```bash
gcloud run services describe pianokt-api --region us-central1 --project pianokt \
  --format="value(spec.template.spec.containers[0].env)"
```

**修复：** 确认 tfvars 里的 `frontend_origin` 正确后重新 apply。
Cloud Run 会滚一个新 revision，桶 CORS 即时更新，**前端不需要重新部署**。

**已知限制：** 只支持一个 origin。Vercel 预览域名和 localhost 都会被挡。

## 12.4 后端：503 `Access policy unavailable`

**报错位置：** `backend/pianokt_backend/api.py`

```python
allowed = httpx.post(SUPABASE_URL + '/rest/v1/rpc/check_user_allowed', …)
if allowed.status_code != 200: raise HTTPException(503, 'Access policy unavailable')
```

**真正的原因通常是 `002_auth_whitelist.sql` 这个 migration 从来没跑过。**

错误信息说「访问策略不可用」，听起来像网络或权限问题，
完全看不出根因是一个 SQL 文件没执行。

**验证：**

```bash
curl -s -X POST 'https://YOUR_PROJECT.supabase.co/rest/v1/rpc/check_user_allowed' \
  -H 'apikey: YOUR_ANON_KEY' -H 'Content-Type: application/json' -d '{}'
```

返回这个就是确诊：

```json
{"code":"PGRST202","message":"Could not find the function public.check_user_allowed without parameters in the schema cache"}
```

**修复：** 在 Supabase SQL Editor 里执行 `supabase/migrations/002_auth_whitelist.sql`。
它是幂等的（`create table if not exists` + `create or replace function`）。
跑完不会把任何人锁在外面，因为空白名单等于全部放行。

**为什么前端从没暴露这个问题：** 因为它 fail-open（`if (rpcErr) return`）。
白名单机制可以长期完全失效而无人察觉。

## 12.5 后端：403 `Account is not on the access list`

和上一条不同，这次函数存在且返回了 `false`。说明 `allowed_emails` 表里有数据，
但当前用户的邮箱不在里面。

```sql
select * from allowed_emails;
insert into allowed_emails (email) values ('you@example.com');
```

或者清空表来关闭白名单：`delete from allowed_emails;`

## 12.6 数据：`piano_outbox` 全是未发布

**现象：**

```sql
select event_type, count(*), count(published_at) from piano_outbox group by 1;
-- published 列全是 0
```

**原因：** `schedules_paused = true`，relay 从来没运行过。

```bash
gcloud scheduler jobs list --location us-central1 --project pianokt
# STATE 显示 PAUSED
```

**后果是级联的：** outbox 不发布 → Pub/Sub 收不到消息 → worker 不被触发 →
`piano_attempts` 永远停在 `UPLOADED` → 对齐结果不存在 → 数据湖一张表都没有。

**手动跑一次：**

```bash
gcloud run jobs execute pianokt-relay --region us-central1 --project pianokt --wait
gcloud run jobs execute pianokt-pipeline --region us-central1 --project pianokt --wait
```

**启用定时：** 把 tfvars 改成 `schedules_paused = false` 再 apply，或者：

```bash
gcloud scheduler jobs resume pianokt-relay --location us-central1 --project pianokt
gcloud scheduler jobs resume pianokt-pipeline --location us-central1 --project pianokt
```

## 12.7 数据：`piano_outbox` 里看不到「结束」事件

**现象：** 查 `event_type` 只看到一片 `practice.event`，分不出开始、暂停、结束。

**这不是 bug。** 触发器把所有行为事件的 `event_type` 统一写成 `'practice.event'`，
真正的动作在 payload 里：

```sql
select payload->>'action' as action, count(*)
from piano_outbox where event_type = 'practice.event' group by 1;
```

**如果 action 里确实缺少 `finished` / `exited`**，那才是真问题。
在修复之前的版本里，终止流程存在一个竞态窗口：上传期间用户按空格会
往已终止的 session 写 `resumed`，而 `exited` 是上传完才写的，
导致事件顺序错乱甚至丢失。当前版本已经通过 `beginTerminal()` 同步释放 session 修复。

## 12.8 数据：有 `play_events_raw` 但没有 `user_play_logs`

**原因：聚合只在终止事件到达时触发。**

```ts
const isTerminalEvent = TERMINAL_PLAY_EVENT_TYPES.has(params.eventType)
if (!isTerminalEvent) return { ok: true }
const finalizeResult = await finalizeUserPlayLog(params.sessionId)
```

一个 session 如果只有 `play_started` 和 `paused`（用户直接关了浏览器标签页），
永远不会被聚合。

**找出这些孤儿 session：**

```sql
select e.session_id, count(*) as events,
       bool_or(e.event_type in ('finished','exited','failed')) as has_terminal,
       min(e.created_at), max(e.created_at)
from play_events_raw e
left join user_play_logs l on l.session_id = e.session_id
where l.session_id is null
group by 1 order by 4 desc;
```

`has_terminal = false` 的是正常的孤儿（用户没正常退出）。
`has_terminal = true` 但没有 log 的，说明聚合 RPC 失败了——
这正是 `finalizeUserPlayLog` 被单独导出的原因，可以手动补：

```sql
select public.upsert_user_play_log('这里填 session_id'::uuid);
```

注意这个函数依赖 `auth.uid()`，在 SQL Editor 里直接跑会因为
`v_user_id is null` 而立刻返回。需要以对应用户的身份调用，
或者临时改造函数接受显式 user_id。

## 12.9 SQL：`42803: column "e.user_id" must appear in the GROUP BY clause`

**这是历史问题，当前版本已修复**，记录在此供参考。

早期的 `upsert_user_play_log` 在聚合 SELECT 里同时出现了聚合函数和裸列：

```sql
-- 错误写法
select min(e.song_id), max(e.created_at), count(*), e.user_id
from play_events_raw e where e.session_id = p_session_id;
```

PostgreSQL 要求裸列必须在 GROUP BY 里。修复方法是先把 `auth.uid()` 存进变量，
用它做 WHERE 过滤而不是 SELECT 输出：

```sql
v_user_id := auth.uid();
if v_user_id is null then return; end if;
select min(e.song_id), max(e.created_at), count(*)::integer
into v_song_id, v_ended_at, v_events_count
from public.play_events_raw e
where e.session_id = p_session_id and e.user_id = v_user_id;
if coalesce(v_events_count, 0) = 0 then return; end if;
```

## 12.10 数据：`challenge_recording_id` 是 NULL

**当前版本已修复**，但值得记录完整的根因分析，因为它是一个典型的前端竞态。

**根因：** 终止流程写成了一路 `await` 的异步函数，
`playSessionIdRef` 要到 `finally` 才清空。在上传的几百毫秒窗口里：

- 用户按空格 → `isNewSession` 判定为 `false` → 往已终止的 session 写 `resumed`
- `resumed` 立刻落库，而 `exited` 要等上传完成才写
- 数据库里的顺序变成 `resumed → exited`，聚合结果全部失真

**修复的三个层次：**

1. **同步封存**（`beginTerminal`）：在任何 `await` 之前停播放、导出 MIDI、
   **同步清空 `playSessionIdRef`**。
2. **守卫所有恢复入口**：播放按钮、空格键、Continue 按钮都检查 `terminalFlowInFlightRef`。
3. **数据库兜底**（migration 007）：metadata 里没有 recording ID 时，
   按「同用户 + 同歌曲 + 时间窗口内 + 距离结束最近」匹配。

**验证当前是否还有问题：**

```sql
select l.session_id, l.play_mode, l.exit_status, l.challenge_recording_id, l.ended_at
from user_play_logs l
where l.play_mode = 'challenge' and l.challenge_recording_id is null
order by l.ended_at desc limit 20;
```

challenge 模式下大量 NULL 说明关联逻辑仍有问题。

## 12.11 UI：退出时卡住，且期间还能继续弹

**当前版本已修复。** 根因是 `handleExitChallenge` 把 `player.stop()` 和
`navigate('/')` 排在了 `await` 之后：

```ts
// 修复前
const recordingId = await saveChallengeRecordingFromBytes({…})   // 4 个网络往返
await emitPlayEvent('exited', {…})                                // 又 1 个
player.stop()      // ← 太晚了
navigate('/')      // ← 太晚了
```

用户要等 5 个网络往返才离开页面，而且这期间播放器还在跑
（TopBar 的返回按钮路径没有先暂停）。

**修复后：**

```ts
const handleExitChallenge = (reason: 'back_button' | 'confirm_exit') => {
  const terminal = beginTerminal(nowSongSec(), { stopPlayback: true })
  if (!terminal) { player.stop(); navigate('/'); return }
  navigate('/')                                    // ← 立刻返回
  void finalizeSession('exited', terminal, { reason })   // ← 后台跑
}
```

客户端路由不销毁 JS 上下文，所以 detached promise 会正常跑完。
标签页被真正关闭时会中断，但 `saveGcsRecording` 的 sessionStorage 幂等键
保证重进同一标签页会复用同一个 attempt，重试是安全的。

**副作用：** 页面卸载后 toast 无处可显，降级成 `console.info`：

```ts
function showToast(msg: string) {
  if (!isMountedRef.current) { console.info('[Challenge]', msg); return }
  ...
}
```

要在首页也能看到保存结果，需要把上传搬到一个活得比路由更久的 provider 里。

## 12.12 UI：进入挑战页立刻弹出成功弹窗

**当前版本已修复。** 根因是预览窗口离开时全局播放器仍处于 `Playing`，
而挑战页的 `player.setSong()` 内部会调 `stop()`，
于是「playing → stopped」的 effect 被触发，在检查 session 之前就弹了窗。

**修复：** 把 session 检查移到 `setShowSuccessModal(true)` 之前
（现在由 `beginTerminal` 返回 null 统一处理），
并在 `SongPreviewModal` 导航前显式 `player.stop()`。

## 12.13 UI：预览时随便点一下就停止落键

**当前版本已修复。** 预览画布容器上挂了 `onClick={() => player.toggle()}`，
点任何位置都会暂停。已移除，播放控制保留在按钮和空格键上。

## 12.14 排行榜一直显示 unavailable

**注意这条文案属于错误分支，不是空数据分支：**

```tsx
Leaderboard unavailable. Complete challenges to appear here.
```

看到它说明 `fetchLeaderboard` 返回了 `{ error }`，而不是「查到 0 行」。

两个可能：

**A. `get_leaderboard` RPC 缺失或报错。** 验证：

```bash
curl -s -X POST 'https://YOUR_PROJECT.supabase.co/rest/v1/rpc/get_leaderboard' \
  -H 'apikey: YOUR_ANON_KEY' -H 'Content-Type: application/json' -d '{"sort_by":"challenges"}'
```

**B. sticky error。** 组件在成功时不清空 `error` 状态：

```tsx
const refetch = () => {
  fetchLeaderboard(sortByRef.current).then((result) => {
    if ('error' in result) setError(result.error)
    else setData(result.data)          // ← 这里没有 setError(null)
  })
}
```

一旦出现过一次错误（比如刚打开页面时还没建立连接），
后续所有成功刷新都无法把它清掉。配合 realtime 自动 refetch，
这个错误会永久挂在界面上。

**建议修复：** 成功分支加 `setError(null)`，并把展示文案改成显示真实错误，
同时区分「查询失败」和「暂无数据」两种情况。

## 12.15 注册显示「验证邮件已发送」但收不到邮件

**当前版本已修复前端部分。** 原先前端无条件显示成功文案，
现在 `signUpWithEmail` 返回 `{ needsEmailVerification, alreadyRegistered }` 并分支提示。

**真正收不到邮件的原因可能有三个：**

1. **Supabase 关闭了 "Confirm email"。** 此时注册即登录，本来就不发邮件。
   在 Authentication → Providers → Email 里检查。

2. **邮箱已注册。** Supabase 出于**防枚举**考虑，对已注册邮箱的重复注册
   返回和新注册一样的成功响应，但不发邮件。前端通过
   `data.user?.identities` 为空数组来识别这种情况。

3. **没配自定义 SMTP。** Supabase 内置邮件服务有很低的速率限制
   （每小时几封），超限后静默丢弃。生产环境必须配置自己的 SMTP。

## 12.16 worker 反复失败进入死信

**查看死信内容：**

```bash
gcloud pubsub subscriptions pull pianokt-dead-letter-inspection \
  --limit 10 --auto-ack=false --project pianokt --format=json
```

`--auto-ack=false` 很重要，否则看一眼消息就没了。

**查 worker 日志：**

```bash
gcloud run services logs read pianokt-worker --region us-central1 --project pianokt --limit 100
```

**查数据库里的错误码：**

```sql
select id, status, error_code, updated_at
from piano_attempts where status = 'FAILED' order by updated_at desc limit 20;
```

`error_code` 存的是异常类名。常见值：

| error_code | 含义 | 处理 |
|---|---|---|
| `InsufficientAnchors` | 匹配上的音符太少，无法估计时间偏移 | 正常业务结果，学生弹得太少 |
| `ValueError` | MIDI 完整性校验失败 / 音轨映射歧义 / 空 MIDI | 检查输入文件 |
| `NotFound` | GCS 对象不存在 | 上传环节有问题 |
| `RuntimeError` | attempt 被占用或未 finalize | 通常是时序问题，重试即可 |

**注意 `FAILED` 状态的 attempt 会被自动重试**，因为它在可领取列表里：

```sql
where id=%s and (status in ('UPLOADED','FAILED') or (status='PROCESSING' and lease_until<now()))
```

## 12.17 attempt 卡在 PROCESSING

**原因：** worker 领取任务后崩溃，没来得及更新状态。

**自愈机制：** 租约 10 分钟后过期，其他 worker 可以接管。

```sql
select id, status, lease_until, now() - lease_until as expired_for
from piano_attempts where status='PROCESSING' order by lease_until;
```

`expired_for` 是正数说明租约已过期，下次消息投递就会被接管。
如果 relay 是暂停的，就不会有新的消息投递——需要手动跑 relay。

## 12.18 pipeline 报「Pipeline writer already running」

```python
if lock and not lock.execute("select pg_try_advisory_lock(hashtext('pianokt-lakehouse-v1')) as acquired").fetchone()['acquired']:
    raise RuntimeError('Pipeline writer already running')
```

咨询锁没拿到。两种可能：

1. **真的有另一个实例在跑。** 等它跑完。
2. **上一次运行崩溃了，锁没释放。** 咨询锁是会话级的，
   数据库连接断开时会自动释放。如果连接还挂着（比如 Supabase 连接池里的僵尸连接），
   需要手动清理：

```sql
select pid, application_name, state, query_start
from pg_stat_activity where state != 'idle' order by query_start;
-- 确认无误后
select pg_terminate_backend(<pid>);
```

## 12.19 pipeline 报「N failed runs; see ops/failures」

某些 `result.json` 入湖失败。查看失败清单：

```python
from deltalake import DeltaTable
import json
rows = DeltaTable('gs://pianokt-pianokt-lake/ops/failures').to_pyarrow_table().to_pylist()
for r in rows:
    print(r['source_key'], json.loads(r['payload_json'])['error_type'])
```

最常见的 `error_type` 是 `ValueError`，对应质量门禁失败。查看具体的错误清单：

```python
q = DeltaTable('gs://pianokt-pianokt-lake/quality/alignment_runs').to_pyarrow_table().to_pylist()
for r in q: print(r['source_key'], json.loads(r['payload_json'])['errors'])
```

四种可能的 errors 见第 10.4 节。

## 12.20 数据湖是空的

按顺序检查这条链路，任何一环断了后面都不会有数据：

```
piano_outbox 有未发布的行？
    ↓ 是 → relay 没跑（见 12.6）
Pub/Sub 主题有消息流量？
    ↓ 否 → relay 权限问题，查 pubsub.publisher 授权
gs://…-raw/events/ 有 jsonl 文件？
    ↓ 否 → archive 订阅的 IAM 缺失（objectCreator + legacyBucketReader）
piano_attempts 有 READY 的行？
    ↓ 否 → worker 没被触发或一直失败（见 12.16）
gs://…-raw/alignment/ 有 result.json？
    ↓ 否 → worker 写入失败，查 objectCreator 权限
pipeline job 跑过吗？
    ↓ 否 → 见 12.6
ops/failures 有内容吗？
    ↓ 是 → 见 12.19
```

逐级验证的命令：

```bash
gcloud storage ls gs://pianokt-pianokt-raw/events/ | head
gcloud storage ls gs://pianokt-pianokt-raw/alignment/ | head
gcloud storage ls gs://pianokt-pianokt-lake/
gcloud run jobs executions list --job pianokt-pipeline --region us-central1 --project pianokt
```

## 12.21 常用运维命令速查

```bash
PROJECT=pianokt; REGION=us-central1

# 部署基础设施
terraform -chdir=infra/gcp init -backend-config="bucket=YOUR_TFSTATE_BUCKET"
terraform -chdir=infra/gcp plan
terraform -chdir=infra/gcp apply

# 构建推送镜像
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT/pianokt/backend:0.8.2 -f backend/Dockerfile .
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT/pianokt/pipeline:0.8.3 -f backend/Dockerfile.spark .

# 手动执行任务
gcloud run jobs execute pianokt-relay --region $REGION --project $PROJECT --wait
gcloud run jobs execute pianokt-pipeline --region $REGION --project $PROJECT --wait

# 看日志
gcloud run services logs read pianokt-api    --region $REGION --project $PROJECT --limit 100
gcloud run services logs read pianokt-worker --region $REGION --project $PROJECT --limit 100
gcloud run jobs executions list --job pianokt-pipeline --region $REGION --project $PROJECT

# 定时任务
gcloud scheduler jobs list   --location $REGION --project $PROJECT
gcloud scheduler jobs resume pianokt-relay --location $REGION --project $PROJECT
gcloud scheduler jobs pause  pianokt-relay --location $REGION --project $PROJECT

# 死信
gcloud pubsub subscriptions pull pianokt-dead-letter-inspection --limit 10 --auto-ack=false --project $PROJECT

# 单个 attempt 手动重跑（需要在容器内或本地配好环境变量）
pianokt-data worker <attempt_id>

# 全量重建数据湖
pianokt-data pipeline --replay

# 导出训练集
pianokt-data export-training --cutoff 2026-09-01T00:00:00Z --output-key training/v1.json
```

---

## 附录 A：环境变量索引

| 变量 | 位置 | 消费者 | 缺失后果 |
|---|---|---|---|
| `VITE_SUPABASE_URL` | Vercel / `.env.local` | 浏览器 | supabase 为 null，全部功能失效 |
| `VITE_SUPABASE_ANON_KEY` | Vercel / `.env.local` | 浏览器 | 同上 |
| `VITE_PIANOKT_API_URL` | Vercel / `.env.local` | 浏览器 | 录音无法保存 |
| `VITE_PUBLIC_GA_ID` | Vercel / `.env.local` | 浏览器 | 无埋点（可选） |
| `DATABASE_URL` | Secret Manager | api, worker, relay, pipeline | 服务启动即失败 |
| `RAW_ROOT` | Terraform | api, worker, pipeline | 无法读写 MIDI 和结果 |
| `LAKE_ROOT` | Terraform | pipeline | Delta 表无处可写 |
| `CHECKPOINT_ROOT` | Terraform | pipeline | Spark 无法记录进度 |
| `EVENT_TOPIC` | Terraform | relay | relay 崩溃（KeyError） |
| `ENABLE_SPARK_EVENTS` | Terraform | pipeline | 行为事件不入湖 |
| `SUPABASE_URL` | Terraform | api | 鉴权崩溃（KeyError） |
| `SUPABASE_ANON_KEY` | Terraform | api | 同上 |
| `FRONTEND_ORIGINS` | Terraform | api | CORS 拒绝所有请求 |
| `SIGNING_SERVICE_ACCOUNT` | Terraform | api | 签名 URL 生成失败 |
| `PIANOKT_SPARK_JARS` | Dockerfile.spark | pipeline | 运行时下载 JAR，慢且不稳 |

## 附录 B：错误码索引

**`reject_reason`（音符判定）**

| 值 | 名称 | 含义 |
|---|---|---|
| -1 | `not_required` | 这只手这个时刻不需要弹 |
| 0 | `correct` | 正确 |
| 1 | `missing_note` | 漏音 |
| 2 | `extra_note` | 多音 |
| 3 | `wrong_pitch_or_substitution` | 音高错误 / 替换 |
| 4 | `too_early` | 早于 150 毫秒阈值 |
| 5 | `too_late` | 晚于 150 毫秒阈值 |
| 6 | `multiple_or_other_error` | 多种错误叠加 |

**`piano_attempts.status`**

| 值 | 含义 |
|---|---|
| `CREATED` | 已建行，等待上传 |
| `UPLOADED` | 文件已校验，等待对齐 |
| `PROCESSING` | 对齐中，持有租约 |
| `READY` | 对齐完成 |
| `FAILED` | 对齐失败，可重试 |

**`user_play_logs.exit_status`**

| 值 | 来源 |
|---|---|
| `succeeded` | 终止事件是 `finished` 且 `success=true` |
| `failed` | 终止事件是 `finished` 且 `success=false`，或是 `failed` |
| `abandoned` | 终止事件是 `exited` |
| `unknown` | 没有终止事件 |

**HTTP 错误码（后端）**

| 码 | 消息 | 原因 |
|---|---|---|
| 401 | `Bearer token required` / `Invalid session` | JWT 缺失或无效 |
| 403 | `Account is not on the access list` | 邮箱不在白名单 |
| 409 | `Attempt identity conflict` | 同 ID 不同参数 |
| 409 | `Upload not complete` | GCS 对象不存在 |
| 409 | `Upload digest mismatch` | 字节与声明的哈希不符 |
| 413 | `MIDI exceeds 8 MB` | 文件过大 |
| 422 | `Invalid MIDI header` | 不是合法 MIDI |
| 422 | `Settings too large` | settings 超过 16000 字节 |
| 503 | `Access policy unavailable` | `check_user_allowed` RPC 不可用 |
| 503 | `Auth unavailable` | 无法连接 Supabase |
| 503 | `Signed uploads require GCS` | `RAW_ROOT` 不是 gs:// 路径 |

## 附录 C：相关文档

- `docs/PIANOKT_TEACHING_GUIDE_ZH.md` — 录音采集与教学场景的详细说明
- `docs/AUTH_AND_RECORDINGS.md` — 认证流程与录音表的早期设计
- `docs/DEPLOYMENT_AND_ROADMAP_ZH.md` — 部署步骤与路线图
- `docs/CHANGES_ZH.md` — 变更记录
- `docs/VALIDATION_ZH.md` — 验证清单




