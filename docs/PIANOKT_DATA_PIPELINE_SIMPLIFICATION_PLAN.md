# PianoKT Data Pipeline 精简改造手顺（V1）

> 历史阶段记录：本文保留当时的设计/验收信息。2026-11-04 的当前实践已升级为 migration 016、Challenge v2 单表事件与默认 Direct Export；以 [当前架构](ARCHITECTURE_ZH.md)、[数据字典](DATA_CONTRACT_V2_ZH.md) 和 [升级手册](DEPLOYMENT_AND_ROADMAP_ZH.md) 为准。

> 目标：**保留在线链路的可靠性，取消训练数据导出对定时 Spark / Delta Bronze–Silver–Gold 物化的强依赖**。先并行验证、再切换，最后才考虑停用旧组件。
>
> 依据：`REFACTOR_AND_DATA_INTEGRITY_ZH.md`（2026-11-03）及现有 GCP 部署说明。**这是分阶段计划。代码层面的 direct 路径已实现，默认仍为 lake；staging、训练消费切换和停调度尚未完成。逐项实际状态见 [实施记录](DATA_PIPELINE_SIMPLIFICATION_IMPLEMENTATION_ZH.md)。复选框保留为完整验收要求，不能把本地实现等同云端验收。**

## 0. 目标架构与改造边界

```text
浏览器（IndexedDB 封存）
  ├── 通过 API 创建 / finalize attempt；PostgreSQL 事务 + Outbox
  └── 通过签名 URL 上传 MIDI / reference / telemetry → GCS Raw

Outbox → Relay → Pub/Sub → 私有 Alignment Worker（本阶段保留）
  ├── GCS: alignment/<run_id>/result.json（不可变）
  └── PostgreSQL: READY + result_key + alignment_run_id + quality/summary
                + 正式评分 + alignment.completed outbox（同一事务）

          ┌── 在线：API / KT / 推荐（只读已提交且质量有效的结果）
          └── 离线：按需 Export Job
                 PostgreSQL 固定元数据/版本集合 → GCS result.json
                 → 质量筛选 + 有序事件/特征转换
                 → intent.json + 数据文件 + manifest.json（最后提交）

可选未来层：Curated Parquet（仅在重复导出/分析性能有实测瓶颈时）
```

**本轮原则上不动：** 前端录音恢复、`/attempts`/`/finalize` 事务、Outbox/Relay/Pub/Sub、Worker lease fencing、对齐算法、`alignment/events.py` 的 v1.1 KC/标签规则、排行榜评分来源、线上 API 权限。不换消息队列，不新增数据库，不引入另一个 exporter 的并行“真相”。**唯一可能需要的小规模 Worker/DB 扩展**是：如果历史 alignment run 的提交证据目前仅存在 Delta，需增加/复用一个 PostgreSQL 不可变 run 提交登记表，确保切断 Delta 后仍能安全选择旧版本。

**本轮可改：** 离线导出入口的读取路径、必要的解耦模块、测试、部署配置与文档。保留旧 Spark/Delta 作为短期并行验证基线；不要开始就删表或 bucket。

## 1. 先做代码/数据流盘点（只读，不要立刻重写）

- [ ] 从项目根目录检查现有入口，确认哪些已经直接读取 PostgreSQL + GCS，哪些确实依赖 Delta：

  ```bash
  rg -n 'export-attempts|export-training|read_bundle|run-selection|Tables\.snapshot|Tables\.rows|spark|Delta' backend
  rg -n 'pipeline_schedule|relay_schedule|schedules_paused|spark_image' infra/gcp
  ```

- [ ] 阅读并复用：
  - `backend/pianokt_backend/pipeline/selection.py`：cutoff、完成/质量/waiting 过滤、run 选择、时间与排序。
  - `backend/pianokt_backend/publication.py`：`intent.json` → create-only 数据文件 → 最后 `manifest.json` → SHA-256 读取验证。
  - `backend/pianokt_backend/cli.py`：现有 `export-attempts`；若还有 `export-training`，确认其真实入口和数据依赖，不凭名称猜测。
  - Worker 输出的 `result.json`（当前 v1.1 onset events）及 PostgreSQL 的 `READY`、`result_key`、`alignment_run_id` 等字段。
- [ ] 画出实际调用链并记录：`CLI → selection → 数据来源 → 特征转换 → publication`。
- [ ] **若现有 `export-attempts` 已能完全绕开 Spark/Delta**，直接复用它；只补缺失的训练字段映射、测试和运维脚本，**不要再发明 `direct_export_v2`**。
- [ ] 记录当前一次导出的输入数量、耗时、成本、输出 schema、样本数；建立一组固定测试数据和 cutoff/run-selection。

**完成条件：** 清楚指出必须移除的依赖，而不是先凭文件名创建新服务。

## 2. 固化“谁能被导出”的单一契约（必须保留）

`selection.py` 是唯一的版本选择/筛选规则来源；不要在新 exporter 又写一遍不一致的 if。

- [ ] 只选择 **数据库已正式提交**、`READY` 且质量有效的 attempt/run；不读暂存对象、未提交 Gold 行或待处理文件。
- [ ] 选择时固定 **cutoff 与 run 集合**。同一 attempt 如有多个可用 alignment run：**显式使用** `--run-selection selection.json`；未指定且有多个候选时**报错**，禁止默认猜 latest。注意：当前 `piano_attempts.alignment_run_id` 可能只记录“当前 run”，**不能仅靠该字段推断所有历史已提交 run**。
- [ ] 使用现有 canonical 顺序：`learner → UTC started_at → visit ID → visit 内 sequence → attempt ID`；历史记录仅按既定兼容规则回退时间。**先确定顺序/index，再做质量过滤**，允许编号空缺。
- [ ] 读取真实 v1.1 onset events，沿用现有正确性标签、KC 向量、缺失值与词表编码；本轮不重定义 `reject_reason`、KC bin 或 question ID。
- [ ] 训练使用的字段、标签、feature/version、alignment_run_id、cutoff、质量策略及排序来源进入 metadata/manifest；现有主数据与独立 metadata 的边界保持不变。
- [ ] 先核查历史 run 的**提交证明**保存在哪里：是否有可直接复用的 PostgreSQL run 记录；若只有 Delta `ops/completed_runs` 才记录已提交历史 run，就需要在切断 Delta 依赖之前解决这个缺口。**GCS 上存在 `result.json` 不等于 run 已提交。**
- [ ] 最小方案：首版只导出 PostgreSQL `READY` 指针指向的已提交 run；对于要求历史版本的 `--run-selection`，若找不到可验证的提交凭证，**报错而不是静默降级**。如果必须保持现有多 run 选择能力，增加轻量 append-only `alignment_run_commits`（或复用实际存在的等价表），在 Worker 的现有最终提交事务内登记 `attempt_id/run_id/result_key/版本/提交时间`；历史 run 需经校验后回填。这个条件性小改动需单独 migration 与 Worker 事务测试，不把原始 GCS 文件当提交标记。
- [ ] DB 查询使用一致快照（现有 CLI 为 repeatable-read 语义）。**先固定已选的元数据/run/result_key**，再从不可变 GCS 对象读取；不在整个 GCS 下载期间长期持有数据库事务。
- [ ] 如果 GCS 对象不存在、解析失败、版本不符或内容 hash 不符：**明确失败并报告**，不要悄悄跳过后成功发布“缩水数据集”。对于明确不合格/被拒绝的数据，按既定质量规则正常排除并统计原因。

**完成条件：** 同一输入快照、run-selection、规则版本必然得到同一事件集合与同一确定性排序；历史 run 的已提交状态可独立于 Delta 验证，或被明确拒绝而非猜测。

## 3. 实现最薄的“直接导出”路径

原则：**复用现有 CLI、selection、publication 与训练 schema；只替换/绕开不必要的 Spark/Delta 读取层。**

- [ ] 在现有 export 模块中加入一个小型数据读取适配层（只在必要时新增文件）：
  1. 从 PostgreSQL 获取已提交的 attempt/run 清单和上下文；历史 run 通过第 2 节确定的可验证提交索引读取；
  2. 调用现有 `selection.py` 固定合格样本及顺序；
  3. 按 `result_key` 读取不可变 GCS `result.json`；
  4. 校验 schema/version/quality、转换成**原有** attempt 或 training 输出格式；
  5. 交给 `publication.py` 发布，不自行再实现 manifest/hash/覆盖逻辑。
- [ ] 尽量以流式/分批迭代读取 GCS 结果，避免一次将全体学生 JSON 放进内存；按学生有序聚合、输出稳定字节序列。
- [ ] 保留现有 `export-attempts` 参数语义（尤其 `--cutoff`、`--run-selection`、`--output-prefix`）。如果还需要 KT training dataset，优先复用已有转换器，不新建第二套事件定义。
- [ ] **初期不新增新的 Cloud Run Service**：优先利用现有 `pianokt-pipeline` Job 的执行载体或新建轻量 Export Job（若命令/镜像与资源配置不兼容），用普通 backend 镜像运行**按需批处理**。命名、镜像、执行命令和 IAM 需要按实际 Terraform 定义落地；不要盲目复用 Spark job 的旧 command。
- [ ] 不把 dataset export 加入每次 Worker 的事务；研究导出是**按需、显式 cutoff/版本**的操作，与用户在线响应解耦。

**完成条件：** 在不启动 Java/Spark、不开启 Delta 物化 Job 的环境下，能基于新完成的 attempt 从 DB+GCS 生成有效版本化数据集。

## 4. 对照测试（此阶段旧 Spark/Delta 仍保留）

- [ ] 固定一个小而复杂的 fixture：多学生、多 visit、同一 visit 多 attempt、UTC 边界、缺失 started_at、质量拒绝、waiting、重复事件、多个 alignment run、部分未提交数据、旧格式兼容情况。
- [ ] 使用同一套 cutoff + 显式 run-selection，比较**旧路径与直接路径**：
  - 学生、visit、attempt、onset event 数量；
  - learner 内事件时间顺序及 index（包括被过滤造成的空缺）；
  - KC vector / `pid` / `response`、统计分布与目标训练 schema；
  - 每个样本引用的 alignment_run_id 和 feature/schema 版本；
  - 输出的可解析性、训练 loader 能否直接读取。
- [ ] 若旧湖仓结果与新路径不一致，先按**原始 GCS + 已提交 DB + `selection.py` 契约**定位语义差异；旧输出不是绝对正确答案。不得为了表面相同改变正确规则。
- [ ] 失败注入：GCS 缺对象/坏 JSON、跨用户混入、同 ID 不同内容、同 attempt 多 run 且无选择、cutoff 越界、发布中断、同 prefix 相同请求重试、同 prefix 不同内容冲突、manifest/hash 校验失败。
- [ ] 跑旧有测试，至少覆盖 `test_attempt_dataset.py`、`test_publication.py`、`test_core.py` 与相关 selection/CLI 测试；保留原 Spark 集成测试用于旧路径验收，新增直接导出测试。
- [ ] 抽取不少于一次**真实 staging attempt** 做端到端验证；检查数据集可加载并用于一次小规模 AKTFMKC 训练/推理 smoke test。单元测试不能替代真实 GCS/Supabase 验收。
- [ ] 性能对比记录：导出运行时长、GCS 请求/字节、内存峰值、Job 启动耗时、每千 attempt 的估算成本。**没有测量就不要声称一定更快或更便宜**。

**切换门槛：** 数据集语义及顺序正确，完整性失败明确拒绝发布，真实环境通过，且新路径可独立运行。

## 5. 先切读者，再停旧物化（不要直接删除历史）

- [ ] 新导出先写**新** `output-prefix`，绝不覆盖现有不可变数据集；用 `read_bundle` 校验所有 SHA-256。
- [ ] 将实际训练脚本/定时研究任务的数据源改为新 manifest，而不是直接读未提交的 Delta 目录。
- [ ] 监测一段观察期：样本数、最早/最新数据时间、导出失败率、重试、费用、训练指标及操作日志。
- [ ] 确认没有在线 API、报表、实验脚本继续依赖 `Tables.snapshot`/Gold/Silver 后，**只暂停旧 pipeline 的 Scheduler**。注意当前 Terraform 存在 `schedules_paused` 等设置，避免误把 relay 调度一同暂停；修改调度配置前先看 `terraform plan`。
- [ ] 保留 GCS Raw、版本化 result JSON、旧 Delta 和 checkpoint 为**只读回滚/审计资产**；不立刻删除任何 bucket、旧表、历史 dataset、Pub/Sub 或 outbox。
- [ ] 回滚路径：恢复旧 pipeline 调度，并将训练读者切回旧已验证 manifest；不要破坏新/旧数据集的不可变性。若新版本 schema 不兼容，应以新 prefix/namespace 发布而非覆盖。

**完成条件：** 不运行 Spark 时，在线录音/对齐/排行榜/推荐不受影响；离线可独立产出并消费训练数据集。

## 6. 第二阶段才做的事（暂不实施）

- [ ] 如果反复解码原始 JSON 的开销成为实际瓶颈，增加一个**可选的** Curated Parquet 层；用 `attempt_id + alignment_run_id + feature_version` 做稳定身份，重建时不覆盖旧版本。
- [ ] 如果需要高频 SQL 分析、复杂聚合或大规模增量物化，再评估 Delta/Spark 或其他分析系统；是否恢复 Bronze/Silver/Gold 由数据量和分析 SLA 决定。
- [ ] 如果 relay 空跑成本高，先调整调度频率并观测 oldest pending outbox age；**不要在本轮顺手更换 Pub/Sub/Cloud Tasks**。
- [ ] 数据删除/保留策略是另一项独立工程：必须先做逻辑删除/读者过滤，再幂等物理清理；不能直接删 GCS 来“精简”。

## 7. 建议实施顺序（给编码 Agent 的任务拆分）

**PR 1 — Audit + Contract（无行为变更）**
- 输出真实调用图、当前数据 schema/读者依赖、baseline fixtures 和导出性能。
- 确定现有 exporter 是否已经符合“直接 DB+GCS 导出”；若已经符合，明确不重复造轮子。

**PR 2 — Direct Export + Tests（不改变线上默认读路径）**
- 仅补必要的读取解耦和 CLI 适配；复用 selection/publication。若缺少独立的多 run 提交索引，先确定“首版拒绝旧 run”还是实施最小 run registry 迁移，不能悄悄用 GCS 文件存在性当提交证明。
- 测试格式、时间顺序、质量与版本选择、幂等发布、失败注入。

**PR 3 — Parallel Validation + Cutover（配置与运行调整）**
- Staging 真实数据对照；新增 manifest；切换训练读者。
- 只暂停不再需要的 Spark 调度，不删除旧湖仓；核对 Terraform plan。

**PR 4 — Optional Cleanup（稳定后单独审批）**
- 删除**已确认无读者使用**的旧代码路径、无效配置与镜像构建需求；旧对象清理另立保留/审计方案，绝不直接执行破坏性删除。

## 8. 非目标 / 验收清单

**不做：** 同时重写前端、API、Worker、KC 标注、推荐模型、消息队列；不移除 Outbox 的事务保证；不取消 Worker lease fencing；不覆盖或删除历史正式数据；不声称跨系统具备物理原子提交。

- [ ] 新导出不依赖 Spark/Delta 才能运行。
- [ ] 只处理已提交、质量有效、版本明确的 attempt/run。
- [ ] 事件顺序、KC、标签、元数据与现有契约一致。
- [ ] `intent.json` → create-only 文件 → 最后 `manifest.json`；读取校验 SHA-256。
- [ ] 重试幂等，冲突必报错，失败不产生可见的半成品数据集。
- [ ] 线上 API/Worker/Relay 与正式排行榜行为未改变。
- [ ] Staging 端到端 + 训练 loader smoke test 通过。
- [ ] 训练读者切换完成，旧 Spark 仅暂停、未删除，回滚方式验证可行。

### Codex 执行提示（可直接复制）

> 先检查现有代码而非直接编码。遵守 `REFACTOR_AND_DATA_INTEGRITY_ZH.md` 和 `.cursor/rules/data-integrity.mdc`。按上述 PR 1→3 实施：把训练导出变为直接读取已提交 PostgreSQL attempt/run + 不可变 GCS result.json，复用 `pipeline/selection.py`、`publication.py` 和现有训练 schema；不要重复造 exporter，不改变在线 API/Outbox；原则上不改 Worker，除非经审计确认需要在其现有事务内登记可验证的历史 run 提交元数据；不删除 Spark/Delta 和历史数据。每一步列出实际改动文件、测试与结果、现有行为差异、回滚方式；必要的 CLI/参数请先根据项目代码确认，不要臆造。没有通过本地及 staging 验收前，不修改生产默认读取路径或暂停任务。
