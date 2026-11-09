# PyKT development 推荐引擎 MVP

实现于 `feature/3_layer_kc`，基于 `66ad76306fee6e912d8b88702900d2dd266e906b`。默认 `/recommendations` 仍使用 DemoRecommender。新策略只有显式 development/test 配置才能进入，失败会返回 409/503，不会伪装成非 Demo 的降级结果。没有修改前端、训练、权重、词表、生产表结构，也没有执行部署或生产写入。

## 审查发现与处理

旧路径只取最近三次摘要，无法证明模型历史完整。本实现按已认证 user_id 查 learner_key，读取当前环境全部 READY 的已提交结果，并独立同步、保存来源摘要和批次日志。

现有曲库是独立 segment MIDI，不含完整原曲起止范围。provider 从 Supabase `piano-midi` 下载，要求按 segment_id + MIDI hash 绑定的人工审计上下文 sidecar；没有证明则逐项跳过。不能以 `segment_index=1` 或文件名猜测文件就是完整 reference。

旧 rollout 没有 forecast_candidates。扩展后保留 non-retained 行为，增加部署限制验证；整轮推荐开始/结束及每次模型响应均绑定同一 state，最终来源检查后的 state 也再检查一次。

## 文件与入口

- `pykt_catalog.py`：读取 published catalog 的查询、Storage 流式限量下载、SHA-256、reference 缓存、逐曲状态。
- `pykt_history.py`：按用户读取 READY 快照、复用 CommittedResults 校验、排序、同步日志、绑定校验。
- `pykt_sync.py`：独立历史同步 CLI，默认 dry-run。
- `pykt_recommendation.py`：Sweet Spot、固定目标分布、预测 Gain、Top 5 及诊断信息。
- `pykt_runtime.py`：开发环境限制、golden 与 manifest 实时校验、认证用户接线。
- `pykt_acceptance.py`：无需 DB 的 mock 验收、真实 PyKT 诊断验收、已绑定用户只读验收。
- `pykt_integration.py`：新增 forecast、include_steps、部署 rollout 限制及 prediction_calls 计数。
- `api.py`：显式开关分支；复用认证、recommendation_id、piano_recommendations 存储，feedback 结构不变。

上述 Python 文件均位于 `backend/pianokt_backend/`；测试为 `backend/tests/test_pykt_recommendation.py`。

## 算法与参数

默认配置：Sweet Spot `[0.60,0.80]`，未知 question/field ratio 任一超过 `0.05` 即排除；最多 10 首 shortlist、64 个 target KC、8 条轨迹、seed=7、Top 5。

先聚合全部合格 reference Onset 做批量预测，预测层按原始 JSON 去重及部署 max_candidates 分批。每首准确率是**还原后所有 Onset**的平均值，重复出现的 KC 重复计权。Sweet Spot 两端包含边界，浮点比较仅容忍 `1e-12` 的数值误差。通过区间筛选后按距离中心、song_id、segment_id 排序进入 shortlist。

T 从全部合格 reference 的 Onset 频率形成，与待评估曲目无关。当不同 KC 不超过 64，保留精确频率；超过时，用固定 seed 在排序后的 KC 上按频率有放回抽样 64 次，重复抽样合并成权重。因此目标数不超过 64，权重和为 1。同一请求所有候选共享 target IDs、权重、seed 与 target_set_digest。

预测 T 得到 p_before。每首完整候选调用 sample rollout，传相同 forecast_candidates、`include_steps=false`、`retain_branch=false`。收益为：

```text
gain(song) = Σ target.weight × (forecast_statistics[target_id].mean - p_before[target_id])
```

保留负收益；按 gain 降序，song_id、segment_id 决定并列顺序。返回最多五首，不为了凑数突破 Sweet Spot。gain 是模型模拟的预测收益，不代表真实教学效果。

检查 max_candidates、max_rollout_steps、max_trajectories、max_rollout_work、max_output_steps 及 schema 上限。超过整首 rollout 限制的曲目返回 `full_song_rollout_limit`，不会截短。超过 forecast 限制不会为某首更换 T。

每曲返回身份、不可变 song_version=MIDI SHA-256、准确率、Gain、rollout 统计、unknown ratios、rank/score/reason。全局包含模型/特征/labeler/evidence 版本、student/revision、来源绑定、cold_start、T、调用计数、候选状态和 skip 原因。

## 历史同步与保护

`student_id = pianokt:{development|test}:{learner_key UUID}`。learner_key 仅从已认证 user_id 对应的 `piano_learner_keys` 查询；CLI 是开发管理员工具，显式指定 user_id，不能对外暴露为匿名端点。

在 repeatable-read/read-only 事务内读取同一用户、同一环境的全部 READY commits 和 finalized practice context。复用 CommittedResults 校验 result_key、result_digest、learner、alignment run、reference/performance 版本、schema、summary 和事件数组。拒绝缺失上下文、混合/非 1.2.0 labeler、字段乱序、非法 JSON 类型或标签。质量无效及 waiting 的记录保留在来源摘要中，但不发送给 PyKT。按现有 attempt_order_key（learner、UTC started_at、visit_id、visit_attempt_sequence、attempt_id）以及 Onset 顺序同步；response 为 `1-reject_reason.event[i]`。

默认 dry-run 不发送 observe，不创建同步日志。显式 `--apply` 才在受控开发 PyKT 中发送 observe-batch；不会改 PianoKT 数据库。日志目录按 student_id 哈希隔离、进程文件锁串行化、原子替换/fsync。每批发送前持久化事件/批次身份；expected_revision 是批次起始游标。批次最大为部署 max_batch_events；事件 ID 稳定为 attempt_id + onset index，批次 ID 是内容摘要。丢失 ACK 后保留原批次并重试，验证服务器的 duplicate 回执和最终 revision。

只允许旧事件/已同步来源前缀完全一致的追加。拒绝历史更正、重排、已有非空远端历史却没有本地证明、模型/词表/endpoint 变化、远端额外事件；不会自动 rebuild 或改写。成功记录完整源快照摘要、event_count、模型/特征/evidence、最终 state。推荐前与结束重新读取 READY 来源并验证绑定；单纯 revision>0 不够。无 READY 历史也需要明确建立 event_count=0 的绑定，并返回 cold_start=true。

日志含开发用户的原始 KC/response，应保存在仅后端可访问目录，不能提交到 Git。单实例独占写入是 MVP 的运行前提：PyKT API 没有可查询的远端事件日志摘要，无法仅凭现有 state 证明“另一服务替换了数据库但恰好保持相同 revision”的情形。生产化之前必须增加服务实例/历史摘要身份与集中式 provenance；不要删除日志后用非空模型状态强行认领。

## 配置与运行

所有命令从仓库根目录运行。不要把服务 token 放到前端或报告中。

```bash
export PYTHONPATH=backend
export PIANOKT_ENV=development
export PYKT_DEV_ENABLED=1
export PYKT_URL=http://localhost:8000
export PYKT_GOLDEN_FIXTURE=backend/tests/fixtures/pykt/golden.json
export PYKT_COMPATIBILITY_MANIFEST=/tmp/pykt-compatibility.json
# 需要认证时另外通过后端环境设置 PYKT_API_TOKEN
.venv/bin/python -m pianokt_backend.pykt_verify "$PYKT_GOLDEN_FIXTURE" \
  --url "$PYKT_URL" --output "$PYKT_COMPATIBILITY_MANIFEST"
```

运行时不仅检查 manifest 文件，还重新执行既有 golden 并比较 manifest；不能手写一个哈希绕过验证。每次操作的 golden 成本不包含在 RecommendationService 的 latency_ms 中。

无需 DB、无网络的离线验收：

```bash
PYTHONPATH=backend .venv/bin/python -m pianokt_backend.pykt_acceptance \
  --mock --output /tmp/pykt-mock.json
```

真实模型、独立诊断学生、生成的三首 MIDI、mock Storage（**不是**真实 Supabase 全库）：

```bash
.venv/bin/python -m pianokt_backend.pykt_acceptance \
  --live-diagnostic --output /tmp/pykt-live-default.json
# 仅为验收 forecast 链路，显式放宽诊断参数；不会改 API 默认配置
.venv/bin/python -m pianokt_backend.pykt_acceptance \
  --live-diagnostic --wide-sweet-spot --diagnostic-unknown-threshold 1 \
  --output /tmp/pykt-live-chain.json
```

访问实际开发数据库/Storage 前，另行配置：

```bash
# 指向已确认的 development/test 数据库；两值必须完全一致
export PYKT_DEV_DATABASE_URL='<development database connection>'
export DATABASE_URL="$PYKT_DEV_DATABASE_URL"
export SUPABASE_URL='<development Supabase URL>'
export RAW_ROOT='<current environment committed result root>'
export PYKT_SYNC_DIR='<private backend local directory>'
export PYKT_CATALOG_PROVENANCE='<audited JSON sidecar path>'
```

sidecar 示例（需要真实审计证据，不要批量填充虚假声明）：

```json
{
  "<published segment UUID>": {
    "midi_sha256": "<actual MIDI SHA-256>",
    "kc_labeler_version": "1.2.0",
    "context": "standalone_reference",
    "evidence": "<why this file is an independent reference with correct training context>"
  }
}
```

当前只支持已证明独立上下文的 reference。若 midi_metadata 标明 requires_full_context，或需要完整原曲但没有 provenance，均跳过。后续可增加 hash-bound 完整原曲/范围证明；本阶段不推断或写数据库。Storage URL 固定为当前 Supabase 的 `/storage/v1/object/public/piano-midi/midi/{sha256}.mid`，拒绝非规范路径、不跟随重定向，15 秒 HTTP 超时，流式上限 10 MiB 并验哈希。

```bash
# 默认 dry-run，检查输出后再显式 apply
.venv/bin/python -m pianokt_backend.pykt_sync --user-id '<development authenticated user UUID>'
.venv/bin/python -m pianokt_backend.pykt_sync --user-id '<development authenticated user UUID>' --apply

# 实际已绑定用户只读验收：不写 recommendation 表，不自动 observe
.venv/bin/python -m pianokt_backend.pykt_acceptance \
  --bound-user '<development authenticated user UUID>' --output /tmp/pykt-bound-user.json

# 显式切换开发 API；不设置时仍是 Demo
export PIANOKT_RECOMMENDER=pykt
# 可选 JSON；未设置则用上述默认参数
export PYKT_RECOMMENDATION_CONFIG='{"sweet_min":0.6,"sweet_max":0.8,"unknown_threshold":0.05,"shortlist":10,"target_count":64,"trajectories":8,"seed":7,"top_k":5}'
```

`POST /recommendations` 仍经现有 user() 认证，成功后才以该用户身份写 piano_recommendations，返回 recommendation_id 与 items，现有 feedback 继续验证 song_id + segment_id。模型不可用返回 503；版本、绑定、源校验问题返回 409，不生成伪装的 fallback。Token 不会发送到 Storage。

## 已执行验收与报告

2026-11-09：实际服务的 model/feature 与第一阶段一致，部署信息/OpenAPI 重新读取，既有 129-Onset golden 验证成功。最终定向回归为 **77 passed, 1 skipped**；skip 是需环境变量显式启用的第一阶段 live pytest 项，真实 golden 已通过 CLI 单独执行。

- 成功：离线算法、Storage/DB mock、状态绑定、dry-run、丢 ACK 重试、幂等、追加/冲突、用户隔离、API 成功持久化与拒绝路径测试。
- 成功：真实 PyKT 默认参数验收。3 首生成诊断曲目均因 unknown_question_ratio=1/3 超过 5% 被排除，rollout_count=0，items=[]，state 不变。这是正确过滤，不是推荐成功三首。
- 成功：显式诊断参数 `[0,1]` / unknown_threshold=1 的真实链路。3 首、9 个 Onset、6 个 unique KC、1 次 batch predict、同一 T、每首 8 条轨迹，3 次 non-retained rollout，revision=0 前后不变。耗时约 471 ms，仅代表小诊断集，不是全库性能。
- Skip：当前环境未配置 PYKT_DEV_DATABASE_URL、PYKT_CATALOG_PROVENANCE、PYKT_SYNC_DIR，因此未访问实际开发 Supabase、未导入任何真实学生历史、未执行真实用户推荐写入。可用上面的显式命令验收，缺少绑定时真实用户路径拒绝执行。
- 未实施（生产化范围）：生产发布/迁移、前端变更、完整上下文片段 provenance 导入、跨进程缓存、分布式同步锁、远端历史摘要、真实教学效果评估。

原始 JSON 报告：

- [离线 mock](pykt_acceptance/mock.json)
- [真实模型默认参数](pykt_acceptance/live-default.json)
- [真实模型放宽诊断参数链路](pykt_acceptance/live-diagnostic-chain.json)

真实诊断返回的第一项摘要（完整证据以 JSON 报告为准）：

```json
{
  "execution_mode": "live_pykt_diagnostic",
  "cold_start": true,
  "history_revision": 0,
  "catalog_total": 3,
  "rollout_count": 3,
  "state_unchanged": true,
  "items": [{
    "song_id": "00000000-0000-0000-0000-000000000002",
    "segment_id": "00000000-0000-0000-0000-000000000066",
    "title": "Diagnostic 2",
    "rank": 1,
    "predicted_accuracy": 0.5119506418704987,
    "predicted_learning_gain": 0.13019506695369878,
    "unknown_question_ratio": 0.3333333333333333
  }]
}
```

## 本地回归

```bash
PYTHONPATH=backend .venv/bin/python -m pytest \
  backend/tests/test_pykt_recommendation.py backend/tests/test_pykt_integration.py \
  backend/tests/test_api.py backend/tests/test_direct_export.py -q

# 第一阶段真实 golden 测试显式 opt-in；其余测试不需要 DB 或模型
PYTHONPATH=backend PYKT_GOLDEN_FIXTURE=backend/tests/fixtures/pykt/golden.json \
.venv/bin/python -m pytest backend/tests/test_pykt_integration.py -q
```

有限小曲库的确定性 golden test 验证 `[0.6,0.6,0.8]` 按 Onset 得到 `2/3`，而非去重平均 `0.7`；精确验证固定 T 加权 Gain 为 `+0.04` / `-0.02`、重复歌曲一致、输入顺序变化不改变排名和 target digest。测试同时覆盖区间端点、未知比例、全部部署 rollout 上限、批量预测拆分、并发 revision/模型/来源变化及 HTTP 错误。
