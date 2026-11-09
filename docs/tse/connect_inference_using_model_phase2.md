```markdown
# PianoKT × PyKT — Codex Phase 2 Prompt

> 目标分支：`feature/3_layer_kc`
> 核对的远程提交：`66ad76306fee6e912d8b88702900d2dd266e906b`（2026-11-09；执行前以本地最新 HEAD 为准）

你正在修改 PianoKT 仓库的 `feature/3_layer_kc` 分支。请基于当前本地 HEAD 的真实代码工作，先确认 `git status`，不要覆盖未提交内容。

## 目标

实现一个可在 development 环境真实运行并验收的 PyKT 曲目推荐引擎 MVP：

**全曲库 reference KC → batch predict → Sweet Spot → non-retained Monte Carlo rollout + forecast_candidates → 固定目标分布上的 Expected Gain → Top 5 推荐。**

保留现有 `DemoRecommender` 为默认，不自动上线新策略。

## 必须先阅读

- `backend/pianokt_backend/pykt_integration.py`
- `backend/pianokt_backend/pykt_verify.py`
- `backend/pianokt_backend/api.py`
- `backend/pianokt_backend/inference.py`
- `backend/pianokt_backend/pipeline/direct.py`
- `backend/pianokt_backend/pipeline/attempts.py`
- `backend/pianokt_backend/pipeline/selection.py`
- `backend/pianokt_backend/song_import.py`
- `backend/pianokt_backend/online.py`
- `backend/pianokt_backend/storage.py`
- `backend/pianokt_backend/provenance.py`
- `src/features/data/catalog.ts`
- `supabase/migrations/018_song_versions.sql`
- `supabase/migrations/019_practice_catalog_identity.sql`
- `docs/PYKT_INTEGRATION.md`
- `backend/tests/fixtures/pykt/deployed_openapi.json`
- `backend/tests/fixtures/pykt/deployed_model_info.json`

## 第一步：确认 PyKT 兼容性与历史状态

1. 使用已有 golden manifest，禁止伪造或跳过兼容性验证。
2. `learner_key` 从 `public.piano_learner_keys` 按已认证 `user_id` 取得，`student_id` 用含环境的稳定命名空间隔离。
3. 提供独立 development/test 历史同步 CLI。读取当前环境的 `READY`、质量有效、非 waiting、labeler=1.2.0 的已提交 `result.json`；核对 `result_digest`、`result_key`、`learner_key`、版本和 event 数组。按照现有 `attempt_order_key` 定义及 Onset 顺序构造事件，`response=1-int(reject_reason.event[i])`。
4. 同步必须显式 `--apply`，默认 dry-run，不在 GET/POST 推荐请求中自动 observe。通过 PyKT `observe-batch` 的 `expected_revision`、稳定 `attempt_id/event_id` 实现有序和幂等，分批遵守服务 `max_batch_events`；任何历史冲突、混合 labeler、缺失上下文必须拒绝，不改写已有历史。
5. 开发环境保存可核验的 sync provenance（源数据快照摘要、event count、model/feature version、最终 revision）。推荐前必须验证该绑定与当前 READY 历史及 PyKT state 一致。不能仅凭 `history_revision>0` 就判定同步完成；空历史要显式 `cold_start`，不能假装已有学生的掌握状态。不要修改生产数据库，不自动运行迁移。
6. 如果无法在现有源数据与 PyKT API 下安全实现上述绑定，明确停止真实学生推荐路径，报告阻塞项；仍可用独立诊断学生完成只读算法测试。

## 第二步：构建 CatalogCandidateProvider

1. 从 `public.piano_public_song_catalog` 读取当前已发布的 `song_id`、`segment_id`、`segment_index`、`title`、`midi_sha256`、`storage_path`。
2. 后端从现有 Supabase `piano-midi` Storage 下载对应 MIDI，而不是把曲库误当成 `RAW_ROOT`/GCS。验证允许的存储路径、文件大小与 SHA-256，限定超时，失败单独记录。
3. `song_version` 使用不可变 MIDI SHA-256；当前一个 segment 对应一份独立 MIDI，不假设数据库已经有完整原曲和绝对起止秒。
4. 使用现有 `ReferenceCandidates.extract()` 与冻结的 v1.2.0 labeler，不复制 KCEncoder，不重写特征公式。对确实需要完整原曲上下文却缺乏 provenance 的片段应拒绝，不能用截断 MIDI 假冒全曲。
5. 按 MIDI hash + labeler version 缓存 reference KC，保留 Onset 顺序、重复 KC、song/segment 身份。
6. 每个曲目输出候选状态、Onset 数、`unknown_question_ratio`、`unknown_field_ratio` 和跳过原因。

## 第三步：实现 RecommendationService

1. 聚合所有合格 segment 的 Onset，调用 `PyKTClient.predict(student_id, all_rows)`，利用已有去重和 `max_candidates` 分批。
2. 还原每首曲目的 Onset 概率，计算 `predicted_accuracy=mean(p_i)`，绝不能对去重后的 KC 直接平均。空曲目和无法预测的曲目应跳过。
3. 默认 Sweet Spot=`[0.60,0.80]`，配置可调。先根据曲目平均正确率及与区间中心的距离筛选，选最多 10 首进入 rollout。未知比例超过可配置阈值（默认 5%）的曲目不参加模型推荐，必须记录原因。
4. 构建所有候选曲目共享、在当前请求内固定的 target KC distribution `T`。初版可从全库 Onset 频率分布确定性抽样最多 64 个目标，记录 `target_set_digest`、权重和 seed。不能给不同候选歌曲使用不同的目标分布，否则 Gain 不可比较。
5. 对 `T` 调用当前状态下的 PyKT predict 得到 `p_before`。扩展现有 `PyKTClient.rollout()` 支持 `forecast_candidates`，保留兼容性、revision 检查和 `retain_branch=false`。
6. 对每首 shortlist 歌曲执行 sample mode 多轨迹模拟，默认 `num_trajectories=8`、固定 seed、`include_steps=false`，并传同一套 `T`。使用 `forecast_statistics[target_id].mean` 计算 `p_after`，不要自行重建模拟 branch 或把模拟 response 写入真实历史。
7. `gain(song)=sum_q weight_q*(p_after(q)-p_before(q))`，按 expected_gain 降序排序，返回 Top 5。gain 可以为负；不可强行截断到 0。若并列则按可复现的稳定身份排序。
8. 严格遵守部署实际限制：`max_candidates`、`max_rollout_steps`、`max_trajectories`、`max_rollout_work`、`max_output_steps`。超过完整歌曲 rollout 限制时明确标记不支持，禁止默默截取前 N 个 Onset 仍声称整首 Gain。
9. 整轮推荐的 `history_revision`、`model_version`、`feature_version`、`evidence_sha256` 必须一致；开始和结束重验 student state，变化时整轮拒绝或从头安全重试，不拼接不同状态的结果。
10. 返回每首曲目的 `predicted_accuracy`、`predicted_learning_gain`、rollout 统计、unknown ratios、`song_id`、`segment_id`、`segment_index`、`title`、`rank`、`score`、`reason`，以及全局诊断摘要和被排除曲目的原因。解释 gain 是模型模拟的预测收益，不能当作真实教学效果。

## 第四步：安全接入已有后端

1. 将新实现放在清晰的 `backend/pianokt_backend/pykt_recommendation.py`（必要时拆成 catalog provider/history sync），保留已有 `inference.py` 的 `DemoRecommender`。
2. `POST /recommendations` 默认行为完全不变。仅在显式 development/test 配置且已完成状态绑定时使用 PyKT 推荐器。不要默认开启 production。
3. 复用现有 `user()` 认证、`piano_recommendations` 写入、`recommendation_id` 与 `/recommendations/feedback` 契约，保持前端兼容。
4. PyKT 不可用、版本不匹配或学生历史不同步时，返回明确可诊断错误；不得暗中使用 `DemoRecommender` 并标记 `is_demo=false`。
5. PyKT token 仅在后端使用，不传浏览器；数据库/Storage/模型请求不能泄露其他用户数据。
6. 本阶段不修改前端、模型权重、词表、训练流程及生产表结构。

## 第五步：测试与验收

1. 新增 `backend/tests/test_pykt_recommendation.py`，使用 mock Supabase/DB/Storage/PyKT 测试全库加载、hash 校验、类型、去重还原、Sweet Spot 边界、固定 T、Gain 数学、确定性排序、未知率、rollout 上限、并发 revision、版本变化、状态不变、用户隔离、错误路径。
2. 新增显式 opt-in 的本地验收 CLI。离线 mock 必须不需要 DB/真实 PyKT；真实服务模式必须使用显式开发环境配置，不使用生产学生或隐式写操作。
3. 真实验收报告输出：`model_version`、`feature_version`、labeler、`student_id`、`history_revision`、history sync provenance、`catalog_total`、`eligible_count`、`skipped`、`unique_kc_count`、`prediction_calls`、`sweet_spot_count`、`rollout_count`、`target_set_digest`、`top5`、`latency_ms`、`state_unchanged`。
4. 为有限小曲库提供可重复的 golden test，验证全库聚合概率确实按 Onset 平均，并验证 gain 计算等于 `forecast_statistics` 加权期望减去 `p_before`。
5. 新增 `docs/PYKT_RECOMMENDATION.md`，列出配置、操作命令、参数、运行示例、性能、限制和下一阶段的生产化阻塞项。

## 交付要求

- 真正实现并运行可执行的测试，不只写设计文档。
- 先提供代码审查发现，再实施。
- 逐项报告成功、skip、未完成。
- 不执行生产数据库写入、生产数据迁移、真实学生状态重建或部署。
- 如果 PyKT live 服务不可连接，不要伪造通过结果，完成离线测试并明确列出真实验收命令。
- 完成后给出改动文件、测试结果、样例推荐 JSON、如何本地复验以及尚未实现的事项。

```

