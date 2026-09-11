# 相对原 PianoKT 的变更清单

以用户上传的 pianokt(1).zip 为基线，按同名文件内容 SHA-256 比较。保留原应用目录、播放器主体、歌曲与音色资源；新增 Python 后端和云端数据管道。下方为实际文件集合，不是计划清单。

新增 48 个文件，修改 11 个文件，保留 465 个同内容文件；业务源文件删除 0 个。构建产物、依赖目录、Git 历史、本地数据、状态和真实环境变量文件不纳入比较或交付。

## 修改的含义

| 位置 | 实际变化 |
|---|---|
| Challenge 保存入口 | 同时创建参考快照和演奏文件，保存终止播放位置及练习上下文，调用 GCS 上传链路 |
| challenge-history/api.ts | 删除新录音的 Supabase Storage 上传实现，导出 GCS 保存函数；旧录音只保留下载兼容 |
| MIDI recorder | Challenge 的每个 note-on/off 采样歌曲时钟，减少 100 ms 静默刷新带来的事件时间量化 |
| Recordings 页面 | 增加 alignment 状态与 JSON 下载、演示推荐入口 |
| Auth / Avatar / Leaderboard | 修复原有 TypeScript 联合类型和可空 Supabase 引用检查，未更换原页面结构 |
| analytics | 缺少可选 GA 配置时不再令生产构建中断 |
| package-lock.json | 重新解析并锁定安装后的前端依赖；package.json 的应用依赖声明保留 |
| README / ignore 配置 | 新后端入口、文档导航与本地数据/状态排除 |

## 新增内容的职责

- backend/pianokt_backend/api.py：Supabase token 与原白名单验证、GCS signed URL、finalize、状态与下载、推荐与反馈。
- worker.py / worker_api.py：事务 outbox、lease、超时隔离、真实对齐、不可变结果复用与状态回写。
- alignment/：提取 notebook 函数、MAD 时间校正、matcher 轨号恢复、明确左手单轨处理、逐音符与左右手结果。
- pipeline/：Raw → Bronze → Silver → Gold、Delta MERGE、quality、completed 发布协议、Spark AvailableNow、训练快照。
- supabase 009：在线状态和 outbox 新表/策略/触发器；额外提供专用后端角色与演示曲目种子。
- infra/gcp：私有 bucket、服务账号和 IAM、Pub/Sub 与死信、两个 Cloud Run Service、两个 Job 和 Scheduler。
- tests / scripts / docs：可复现测试、合成 MIDI、打包工具、教学文档和部署路线图。

## 没有交付为真实模型的部分

没有训练 AKT/DKT，没有声称估计了校准能力或学习增益，没有自动生成新 MIDI 曲目。推荐为明确标识的黑盒演示实现。没有在用户云项目执行部署。未来工作与验收条件见部署路线图。

## 实际修改文件

- `.gitignore`
- `README.md`
- `package-lock.json`
- `src/components/AvatarUploadZone.tsx`
- `src/features/analytics/index.ts`
- `src/features/auth/context.tsx`
- `src/features/challenge-history/api.ts`
- `src/features/midi/useSegmentedRecordMidi.ts`
- `src/pages/challenge/page.tsx`
- `src/pages/home/Leaderboard.tsx`
- `src/pages/recordings/page.tsx`

## 实际新增文件

- `.dockerignore`
- `.env.example`
- `.github/workflows/backend.yml`
- `Makefile`
- `backend/.env.example`
- `backend/Dockerfile`
- `backend/Dockerfile.spark`
- `backend/fetch-jars.sh`
- `backend/pianokt_backend/__init__.py`
- `backend/pianokt_backend/alignment/__init__.py`
- `backend/pianokt_backend/alignment/prototype.py`
- `backend/pianokt_backend/alignment/service.py`
- `backend/pianokt_backend/alignment/tracks.py`
- `backend/pianokt_backend/api.py`
- `backend/pianokt_backend/cli.py`
- `backend/pianokt_backend/contracts.py`
- `backend/pianokt_backend/inference.py`
- `backend/pianokt_backend/online.py`
- `backend/pianokt_backend/pipeline/__init__.py`
- `backend/pianokt_backend/pipeline/jobs.py`
- `backend/pianokt_backend/pipeline/spark_events.py`
- `backend/pianokt_backend/pipeline/tables.py`
- `backend/pianokt_backend/pipeline/training.py`
- `backend/pianokt_backend/storage.py`
- `backend/pianokt_backend/worker.py`
- `backend/pianokt_backend/worker_api.py`
- `backend/pyproject.toml`
- `backend/requirements.lock`
- `backend/tests/schema.mjs`
- `backend/tests/test_api.py`
- `backend/tests/test_core.py`
- `backend/tests/test_spark.py`
- `backend/tests/test_two_hands.py`
- `docs/CHANGES_ZH.md`
- `docs/DEPLOYMENT_AND_ROADMAP_ZH.md`
- `docs/PIANOKT_TEACHING_GUIDE_ZH.md`
- `docs/VALIDATION_ZH.md`
- `infra/gcp/main.tf`
- `infra/gcp/terraform.tfvars.example`
- `scripts/make_demo_midis.py`
- `scripts/package_delivery.py`
- `src/features/challenge-history/AlignmentStatus.tsx`
- `src/features/challenge-history/Recommendations.tsx`
- `src/features/challenge-history/gcs.ts`
- `src/features/challenge-history/referenceSnapshot.ts`
- `supabase/backend_role.sql`
- `supabase/migrations/009_lakehouse_backend.sql`
- `supabase/seed_demo_catalog.sql`
