# 本地开发：三条不同的验证路径

更新日期：2026-11-03。先选清楚要验证什么：只看网页、调试对齐算法、还是验证真实保存与异步处理。三者需要的外部服务不同。

## 1. 安装运行环境

在仓库根目录执行。后端声明 Python 精确版本 3.13.12，与两个 Dockerfile 一致。已有 .venv 不会因修改版本文件而自动换解释器。

```bash
python3.13 --version
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.lock
python -m pip install --no-deps -e backend
npm ci --ignore-scripts
```

最后一条适用于网页构建；原媒体渲染脚本的额外原生依赖另行准备。

## 2. 只看网页和浏览器练习

```bash
npm run dev
```

内置曲目的播放与可视化主要在浏览器执行。登录、资料、录音列表需要 Supabase；新的 Challenge 保存还需要 API 和 GCS。只启动 Vite 不会自动启动 Python、数据库或 worker。

根目录没有 .env.example；浏览器模板是 `.env.local.example`：

```bash
cp .env.local.example .env.local
```

VITE_* 是浏览器构建配置，不能放数据库密码、service-role key 或私钥。

## 3. 本地 Alignment Lab：调试一次真实对齐

这是 Challenge 结束时把证据直接发到本地 `/debug/local-align` 的开发分支，绕过正式上传与 worker。

在根目录 .env.local 配置：

```dotenv
VITE_PIANOKT_API_URL=http://127.0.0.1:8000
VITE_LOCAL_ALIGNMENT_LAB=true
```

在 backend/.env 或启动进程环境配置：

```dotenv
PIANOKT_ENV=development
PIANOKT_LOCAL_ALIGNMENT_LAB=1
PIANOKT_LOCAL_ALIGNMENT_OUTPUT_DIR=../data/local-alignment
FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

后端从 backend 目录启动时，上面的相对输出路径对应根目录 data/local-alignment：

```bash
cd backend
set -a
source .env
set +a
../.venv/bin/python -m uvicorn pianokt_backend.api:app --host 127.0.0.1 --port 8000 --reload
```

另一个终端从根目录启动 `npm run dev`。完成或退出一次练习后，页面显示实际对齐卷帘和 events_data，并可下载 JSON；服务器也写出 `<alignment-run-id>.json`。

这个端点本身不依赖数据库、GCS、Pub/Sub 或 pipeline，只在明确 development + lab 开关且没有 K_SERVICE 时开放。整个网页的 AuthProvider/activity 模块没有因此被全局关闭：若配置并登录了 Supabase，仍可能发生登录和活动请求。

Lab JSON 是一次算法调试结果，没有完整的真实学生访问、注册时间、最终汇总和入湖完成上下文，不能直接冒充正式 attempt 训练快照。

## 4. 本地 API + 真正的 GCS/Supabase 保存

测试正式保存路径时，前端关闭 VITE_LOCAL_ALIGNMENT_LAB，重启 Vite。后端模板是 `backend/.env.example`：

```bash
cp backend/.env.example backend/.env
```

填写开发项目的 DATABASE_URL、SUPABASE_URL、SUPABASE_ANON_KEY、RAW_ROOT、SIGNING_SERVICE_ACCOUNT。数据库需要与 migration 014 对齐，执行身份要有 backend 权限。

注意模板启用了本地 lab endpoint，正式保存测试可以设 PIANOKT_LOCAL_ALIGNMENT_LAB=0；是否走 Lab 分支还由前端开关决定。

本地跳过邮箱白名单需要同时配置：

```dotenv
# 浏览器，仅开发构建生效
VITE_PIANOKT_LOCAL_BYPASS_ACCESS_LIST=true
```

```dotenv
# API，仍验证真实 Supabase 身份
PIANOKT_ENV=development
PIANOKT_LOCAL_DEV_BYPASS=1
```

Google OAuth 或邮箱确认回跳在 Supabase 项目中加入实际本地域名，如 http://localhost:5173 与 http://127.0.0.1:5173。本地前端指向 Cloud Run 时，服务端不会因此绕过白名单。

GCS 需要有效 ADC 和签名身份权限，例如开发机通过 `gcloud auth application-default login` 获取凭据，并被授权模拟签名服务账号。raw 桶 CORS 与 API FRONTEND_ORIGINS 都要允许实际前端 origin。凭据取得、IAM 和桶配置以开发项目实际部署为准。

API 启动方式同上；把 `source .env` 中的实际秘密留在本机。正常保存应依次出现 /attempts、两份 MIDI 加 telemetry PUT、/finalize。

### 保存成功后怎样触发后台

本地 API 不会自动附带常驻 relay/worker。可以选择：

- 使用开发云环境已部署的 relay + Pub/Sub + worker。
- 在已加载环境、ADC 和数据库权限的终端手动运行 `pianokt-data worker <attempt-uuid>`，只验证该 attempt 的对齐处理。
- 完整投递链验证还要运行 `pianokt-data relay --limit 1000` 并具备真实 EVENT_TOPIC 和私有 worker 订阅。

手动 worker 与消息投递是不同验证路径。前者不能证明 Pub/Sub push 和归档已经配置正确。

## 5. 完全离线的合成 MIDI 示例

无需登录/真实数据库，可按 [运行与部署](DEPLOYMENT_AND_ROADMAP_ZH.md) 执行 make_demo_midis + align，得到 raw、matching、result 和本地 Delta 表。它验证算法/物化，不能证明真实账号、上传签名或训练上下文完整。

## 6. 时间显示

UTC 是排序与存储的时间坐标。migration 012 提供 v_piano_attempts_jst、v_piano_outbox_jst、v_user_play_logs_jst；新版 Silver/Gold 保留 UTC，同时提供日本日期/时间分析字段。录音页目前固定 Asia/Tokyo 显示。

数据库上传 created_at、attempt 实际 started_at、歌曲位置是不同字段。visit_index/attempt_index 用真实开始时间的 UTC 值排序；时区统一不能修正学生设备本身的时钟误差。
