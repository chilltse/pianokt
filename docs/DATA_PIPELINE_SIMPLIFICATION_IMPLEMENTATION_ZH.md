# 数据链路简化：实际实施记录

> 历史阶段记录：本文保留当时的设计/验收信息。2026-11-04 的当前实践已升级为 migration 016、Challenge v2 单表事件与默认 Direct Export；以 [当前架构](ARCHITECTURE_ZH.md)、[数据字典](DATA_CONTRACT_V2_ZH.md) 和 [升级手册](DEPLOYMENT_AND_ROADMAP_ZH.md) 为准。

日期：2026-11-03。本记录对应本地代码，不表示已经部署或完成生产切换。

## 1. 盘点结论与选择

原有 export-attempts/export-training 都是 CLI 读取 PostgreSQL context → selection 扫 raw/alignment → Delta ops/completed_runs 过滤 → 原有序列化器。训练转换本身没有依赖 Spark，但必须先完成 Delta 物化才能取得完成凭证。在线 API/排行榜/推荐不读取 Delta。

现在增加同一 CLI 的 --source direct，默认仍为 lake。调用链为：

CLI → 一个 repeatable-read 事务内 read_context + read_committed → 关闭事务 → selection.eligible_results 分发读取适配器 → 按 DB 固定 key 读取 GCS → 校验 → 原 attempts/training 转换与发布。

没有另造 exporter，没有新增事件定义、词表、pid 或 response 编码。仓库目前输出 onset examples，并没有 AKTFMKC 训练/loader 实现；不能把导出 JSON 可解析说成真实模型验收通过。

选择方案中的最小当前指针模式，不引入历史 run registry。direct 的 --run-selection 必须指向快照中的当前 READY run。指定旧 run、未提交 run 或超出 cutoff 的 run 都会失败；需要历史多版本时继续使用 lake 基线。不能从 GCS 文件存在推断提交。

## 2. 实际代码改动

- pipeline/direct.py：读取 READY 指针适配层；不导入 Tables，不列举 GCS。保存数据库快照行后逐个下载；核对 SHA-256、attempt/learner/run、输入 MIDI hash、schema=3、summary、版本信息、完成时间、事件数组。缺对象、坏 JSON、身份或 hash 不符直接失败。
- pipeline/selection.py：保留唯一选择入口，向有 results 方法的提交源委派；旧 Delta 路径仍可运行。质量/waiting 排除、重复合格 run 拒绝继续复用。
- cli.py：两个 export 命令新增 --source lake|direct，默认 lake。direct 的上下文和指针来自同一短事务；网络读取不占用数据库事务。
- worker.py + migration 015：最终发布事务新增 result_digest 与 result_committed_at，与 READY、正式分数、outbox 一起提交。摘要使用 Objects.json 相同的 canonical JSON 字节编码。回滚/旧 lease 拒绝继续有效。
- attempts.py：保持主行 schema=1.2、事件 v1.1、编号先于质量过滤；manifest 记录来源、feature/quality 策略，index_map 追加 matcher 版本和结果摘要。
- training.py：保持原单 JSON、onset examples 与排序；增加来源、规则版本和每个 run 的版本/摘要来源信息。单对象仍通过 create-only 原子写入；它不是多文件 manifest bundle。需要 manifest 消费时使用 export-attempts + read_bundle。
- infra/gcp/export.tf：可选 enable_export_job=false；开启后创建按需 pianokt-export，普通 backend 镜像，无 Java/Spark、无 Scheduler。独立身份可读 raw，只能在 datasets/ 前缀创建对象，无删除权限；可读取 DB secret。未提供参数时只显示 help。
- tests/test_direct_export.py：旧/新主数据逐字节对照、UTC 顺序和编号空缺、单 JSON 消费、重复导出、缺对象/坏 JSON/hash/跨学生/schema身份/旧凭证/版本/cutoff 失败不发布。
- tests/activity_db.mjs、test_activity.py：加载 015，验证结果摘要和 READY 同事务回滚及提交。
- README、架构总览、系统参考、导出说明、部署说明、原计划及本记录同步；旧湖仓说明保留为 source=lake 的运行逻辑。



## 3. 数据完整性与实际限制

result_committed_at 是最终事务中记录的数据库时间，不是 PostgreSQL WAL 提交时刻；是否已提交由 repeatable-read 可见的 READY 行判定。cutoff 同时约束该时间和 result 的完成时间。该设计不提供按过去时刻恢复整个数据库的能力，必须保留发布快照。

015 不自动回填旧 READY 的摘要/时间。缺凭证时整体失败，避免静默少导数据；可以显式选取新完成的 run，或使用原 lake 方式导出历史数据。后续历史回填需独立核对提交证据，不能简单对所有对象计算 hash 后标成已提交。

下载按 run 逐个进行，不会预加载整桶对象；现有 exporter 仍会在内存中收集选中的结果/记录和输出字节，尚不是有界内存的大规模流式 exporter。没有测量真实成本、峰值内存、Job 启动时间，故不声称已经更快或更便宜。

多文件 attempt 发布继续使用 intent → create-only 数据文件 → manifest，读者必须 read_bundle 校验。新的 metadata 会改变数据字节，首次对照使用新 prefix，不覆盖任何旧版本。跨 DB/GCS 不存在物理全局事务；无 READY 指针的孤立结果不能直接导出。

## 4. 启用与回滚

先对 staging 应用 migration 015，再发布新 Worker/backend 镜像。应用级旧 Worker 可继续在线运行，但不会生成 direct 所需凭证；应在采集验收样本前更新。没有执行任何云端 migration 或部署。

本机或普通 backend 容器：

```bash
pianokt-data export-attempts --source direct \
  --cutoff 2026-11-04T00:00:00Z \
  --run-selection selection.json \
  --output-prefix datasets/direct-validation-001

pianokt-data export-training --source direct \
  --cutoff 2026-11-04T00:00:00Z \
  --run-selection selection.json \
  --output-key datasets/direct-validation-001-onsets.json
```

selection.json 是 attempt ID → 当前 run ID 的 JSON 对象；配置 DATABASE_URL 和 RAW_ROOT。不需要 LAKE_ROOT、Java 或运行 pipeline。文件中的 cutoff 和选取集合应由实际实验确定。

云端可先 review terraform plan，显式启用 enable_export_job，再执行新 Job 的参数覆盖：

```bash
gcloud run jobs execute pianokt-export --region="$REGION" \
  --args=export-attempts,--source,direct,--cutoff,2026-11-04T00:00:00Z,--output-prefix,datasets/direct-validation-001 \
  --wait
```

执行者需要任务参数覆盖执行权限；不自动授予。示例无 run-selection 会尝试全部 eligible 当前指针，遇到旧无凭证记录会失败。需要选择文件时先通过受控镜像/运行环境提供文件，CLI 不会把本地路径自动上传到 Job。

回滚导出使用 --source lake 和新的输出 prefix 或原已验证 manifest；不删 015 字段、不删数据、无需恢复未曾暂停的调度。enable_export_job 默认 false，本次不改变 relay、pipeline 的 Scheduler，也不切换任何训练读者。

## 5. 验收状态

本地结果见下方追加记录。尚待真实 staging attempt、真实 GCS/Supabase 事务与 IAM、terraform plan、训练 loader/AKTFMKC smoke test、费用/内存/耗时测量和观察期。因此 PR 3 的真实切换门槛未满足，默认路径与旧调度保持原状；PR 4 清理和第二阶段 Parquet 没有实施。

### 本次实际运行结果

- 后端完整测试：53 passed、1 skipped（Spark 单独运行），71.88 秒。包含 PGlite 上最终提交失败回滚和 lease fencing。
- 聚焦数据导出/发布/事务/核心测试：36 passed，18.68 秒；之后调整训练 dataset_version 将来源版本元数据纳入身份，并重跑 direct 测试。
- Terraform fmt 与 validate 通过。沙箱内 provider 握手失败，沙箱外只读 validate 成功；没有执行 plan/apply。
- CLI --help 已核对 source 参数；git diff --check 通过。
- 以上耗时为测试套件耗时，不是生产导出性能基准。前端没有修改，本轮未重跑前端构建。
- direct 专项最终重跑：9 passed，0.64 秒。
- READY/摘要事务、真实 SQL 读取适配器及 cutoff 查询测试最终重跑：1 passed，10.92 秒。
- 旧 Spark 集成测试：沙箱内因 Java 回环 socket 被禁止无法启动；允许本地通信后重跑 1 passed，74.63 秒。
- 文档本地链接检查无断链。当前合计 53 项常规后端测试 + 1 项独立 Spark 集成测试通过。

