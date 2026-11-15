# Data Architecture V3 实施记录

起点：`feature/songs_category_data_pipeline`，commit `230b9a6f8611f700d9eece6a4fbacca61cc4852f`。修改保留在工作区，未自动提交或部署。

## Implemented

旧架构：Raw → Worker → READY，并保留扫描对象 / Delta completed_runs 的第二条导出与定时物化路径。

新架构：活动事实 / Raw → Worker 内存解析 → 原有 Alignment / KC → 不可变 Silver → PostgreSQL Registry / fenced READY 事务 → Registry 唯一选择源 → 按需 Gold bundles。在线与研究 Run 分离，legacy 不进入 V3 Gold / PyKT。

新增 migration 024：Registry、架构标记、输入身份约束、不可变 UPDATE、当前指针约束和排除 tombstones。保留所有既有 migrations，包括 009 核心表。不执行历史回填或数据库重置。

Storage、Registry、Gold 字段和 CLI 参数详见 [架构](DATA_ARCHITECTURE_V3.md)、[契约](DATA_CONTRACT_V3.md)、[导出与 reprocess](DATASET_EXPORT_GUIDE.md)。

删除 Spark Dockerfile / JAR 脚本、pipeline jobs/tables/spark_events、Spark CI。移除 deltalake、pyarrow 和 Spark extras，重新解析 requirements.lock，保留剩余依赖版本；PyTorch 仅为已有实验测试本地安装及 CI 测试使用，不进入运行镜像。

Terraform 目标移除 pipeline 计算资源与 lake/checkpoint 声明，保留现有 Data Bucket 和 API/Worker/Relay 地址，使用路径 IAM，Exporter 无定时调度。旧桶必须先按手册从运行 state 受控分离，不会由本任务执行删除。

## Locally Verified

- 前端类型检查、生产构建通过。
- 活动、上传队列、上传协议、曲库缓存、SongDiscovery、MIDI、Space、Player pause/resume 脚本通过。
- 隔离 PGlite + 本地 Objects + 真实可解析 MIDI 的创建、上传、finalize、Worker、Registry、READY、两类 Gold、manifest/hash 校验通过。
- 重复上传/投递、研究 Run 不替换在线指针、歧义 Run 拒绝、排除防重新引入、过期租约、提交冲突及事务回滚的核心复测 4 项通过。
- 导出/发布回归 28 项通过；实验 benchmark 2 项通过。
- Terraform fmt 和 validate 通过；validate 提示本地未跟踪 tfvars 仍有旧变量，需管理员按 example 更新。

## Real Environment Verified / Deployed

无。未运行生产 migration、apply、重处理、训练、Bucket 删除或部署。

## Not Verified / 限制

- Docker daemon 未启动，无法完成镜像构建。
- Terraform plan 未取得可用结果，云端保留资源是否有 destroy/replace 仍须管理员审核；不能将 validate 当成 plan。
- 没有真实 PyKT 权重/外部训练服务验收；现有模型接口、转换及实验测试不能替代真实模型 smoke test。
- PGlite 不替代独立 PostgreSQL 多连接锁测试；未配置 PIANOKT_TEST_POSTGRES_DSN 的测试使用原有 skip 门禁。
- 不提供自动跨存储永久删除和在线 Promote；研究重处理默认不改变在线成绩或 KT。生命周期及受控重建步骤见 [生命周期](DATA_LIFECYCLE.md)。

生产前必须完成 [Clean Cutover](DATA_ARCHITECTURE_V3_CUTOVER.md)：排空旧 Attempt、停旧消费者、应用 024、切换 DATA_ROOT、部署新镜像、真实验收后恢复流量。旧桶实际退役另行审批。

## 文件清单

状态 M/A/D 分别为修改/新增/删除；历史说明移至 docs/archive/pre_v3，保留参考但不再作为运行指南。

```text
M	.github/workflows/validate.yml
M	Makefile
M	README.md
M	backend/.env.example
D	backend/Dockerfile.spark
D	backend/fetch-jars.sh
M	backend/pianokt_backend/api.py
M	backend/pianokt_backend/cli.py
M	backend/pianokt_backend/pipeline/attempts.py
M	backend/pianokt_backend/pipeline/direct.py
D	backend/pianokt_backend/pipeline/jobs.py
M	backend/pianokt_backend/pipeline/selection.py
D	backend/pianokt_backend/pipeline/spark_events.py
D	backend/pianokt_backend/pipeline/tables.py
M	backend/pianokt_backend/pipeline/training.py
M	backend/pianokt_backend/publication.py
M	backend/pianokt_backend/pykt_acceptance.py
M	backend/pianokt_backend/pykt_catalog.py
M	backend/pianokt_backend/pykt_history.py
M	backend/pianokt_backend/pykt_sync.py
M	backend/pianokt_backend/upload_validation.py
M	backend/pianokt_backend/worker.py
M	backend/pyproject.toml
M	backend/requirements.lock
M	backend/tests/activity_db.mjs
M	backend/tests/test_activity.py
M	backend/tests/test_activity_batch.py
M	backend/tests/test_async_finalization.py
M	backend/tests/test_attempt_dataset.py
M	backend/tests/test_catalog_roundtrip.py
M	backend/tests/test_core.py
M	backend/tests/test_direct_export.py
M	backend/tests/test_outbox_dispatch.py
M	backend/tests/test_pg_concurrency.py
M	backend/tests/test_pg_outbox.py
M	backend/tests/test_reference_features.py
D	backend/tests/test_spark.py
M	backend/tests/test_time_guided_alignment.py
M	backend/tests/test_worker_dispatch.py
M	docs/ARCHITECTURE_ZH.md
M	docs/DATA_CONTRACT_V2_ZH.md
M	docs/DATA_PIPELINE_SIMPLIFICATION_IMPLEMENTATION_ZH.md
M	docs/DEPLOYMENT_AND_ROADMAP_ZH.md
M	docs/LOCAL_DEVELOPMENT_ZH.md
M	docs/PIANOKT_DATA_PIPELINE_SIMPLIFICATION_PLAN.md
M	docs/PIANOKT_SYSTEM_REFERENCE_ZH.md
M	docs/PIANOKT_TEACHING_GUIDE_ZH.md
M	docs/PYKT_INTEGRATION.md
M	docs/PYKT_RECOMMENDATION.md
M	docs/REFACTOR_AND_DATA_INTEGRITY_ZH.md
M	docs/VISITS_AND_ATTEMPT_DATASET_ZH.md
M	docs/pianokt_data_architecture_simplification_plan.md
M	docs/relay_event_driven.md
M	infra/gcp/export.tf
M	infra/gcp/main.tf
M	infra/gcp/terraform.tfvars.example
M	scripts/tse/group_pianokt_by_student.py
A	backend/pianokt_backend/pipeline/gold.py
A	backend/pianokt_backend/reprocess.py
A	backend/pianokt_backend/run_registry.py
A	backend/pianokt_backend/storage_layout.py
A	backend/tests/test_data_architecture_v3.py
A	backend/tests/v3_test_helpers.py
A	docs/DATASET_EXPORT_GUIDE.md
A	docs/DATA_ARCHITECTURE_V3.md
A	docs/DATA_ARCHITECTURE_V3_CUTOVER.md
A	docs/DATA_CONTRACT_V3.md
A	docs/DATA_LIFECYCLE.md
A	docs/archive/pre_v3/ARCHITECTURE_ZH.md
A	docs/archive/pre_v3/DATA_CONTRACT_V2_ZH.md
A	docs/archive/pre_v3/DEPLOYMENT_AND_ROADMAP_ZH.md
A	docs/archive/pre_v3/LOCAL_DEVELOPMENT_ZH.md
A	docs/archive/pre_v3/PIANOKT_SYSTEM_REFERENCE_ZH.md
A	docs/archive/pre_v3/PYKT_INTEGRATION.md
A	docs/archive/pre_v3/PYKT_RECOMMENDATION.md
A	docs/archive/pre_v3/VISITS_AND_ATTEMPT_DATASET_ZH.md
A	scripts/download_gold.py
A	supabase/migrations/024_data_architecture_v3.sql
```

## 最终回归收尾

完整后端运行（未包含单独运行的 torch benchmark）得到 283 passed、60 skipped、4 failed；该运行跨越发布时间幂等修复，前三项为旧进程加载的发布实现，第四项为直接写 READY 的旧夹具。修复完成后对这四项所属的导出、发布、V3 端到端、Worker 投递模块重新运行，结果 **53 passed、16 skipped、0 failed**。相关解析/曲库/核心模块另行复测 **22 passed**；torch benchmark **2 passed**。没有删除或新增任意 skip 来消除失败。没有在最终修改后再次完整运行全部模块，以上分开报告，不伪称一次全量零失败。

最终 `git diff --check` 与 Python compileall 通过。Terraform plan 未得到可审阅计划，已请求中断；未执行 apply。发布前仍需独立 PostgreSQL 并发测试、Docker 构建、真实云端计划及模型服务验收。
