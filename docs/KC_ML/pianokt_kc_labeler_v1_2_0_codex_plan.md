# PianoKT：KC Labeler v1.2.0 重构实施计划（交给 Codex 执行）

> **任务性质：直接修改代码、补测试和文档；不是只给方案。** 先阅读仓库当前代码再实施，避免基于本文假设盲改。目标分支为 `feature/3_layer_kc`；如本地分支或文件已更新，以实际最新代码为准，并在完成报告中列出与本文不一致的地方。
>
> **版本字段要求：本次 KC 相关只使用 `"kc_labeler_version": "1.2.0"`。** 不要新增 `reference_grouping_version`、`outcome_labeler_version`、`kc_config_hash`、`feature_version` 等第二套 KC 版本标识。现有系统协议必需的 `schema_version`、`matcher_version`、`dataset_version` 和 `alignment_run_id` 等原有字段仍按现有契约保留；不要为了这项要求破坏既有协议。

## 0. 目标及非目标

### 目标

1. 把 `alignment/events.py` 中的 **Reference/Onset/KC 计算**抽入 `alignment/feature_extraction.py`，让 `events.py` 专注于 **关联 Alignment 结果并生成 response / reject_reason**。
2. **Reference 分组只有一个权威来源**：继续复用 `time_guided.group_chords()` 的 **30 ms（`0.03` 秒）分组语义**，Feature Extraction 与 Alignment 消费**同一批** `reference_chords`，不各自重新分组。
3. 使用既有 `ref_id`（`r{track}_n{sequence}`）关联 Reference Note 与 Alignment 行。对每一组做 **强制、失败即中止** 的完整性校验，禁止只靠浮点时间合并。
4. 统一采用 `kc_labeler_version = "1.2.0"`，并从实际标注产物一路传播到 `result.json`、`attempts.jsonl`、训练导出及 Dataset Manifest；不得硬编码旧 `v1.1` 或把旧结果伪装成 v1.2.0。
5. 保留当前已观测区间（`observed_end_sec`）的语义：学生没弹到的后续音符**不应被计为漏音**。保留既有 `events_data` v1.1 形状：`kc_feature_names`、`onset_seconds`、`pitches`、`intervals_seconds`、`kc_vectors`、`reject_reason.event/note`、`timing_offset_ms`、`pitch_offset_semitones`、`extra_notes`，以及现有错误码 0–4。
6. 新数据明确使用新的 KC 版本及 30 ms Onset 语义，**不得声称新 v1.2 与旧 v1.1 全部事件逐条相同**。对于没有 30 ms 近同时起音的普通用例，应尽量保持当前输出一致。

### 非目标

- 不重写 `align_chords()` 的匹配算法、遥测协议、前端、上传流程、Supabase 表结构或评分公式。
- 不立即部署 GCP / 改生产配置 / 批量回填历史数据。
- 不引入额外的多层版本管理平台、复杂插件体系或强制 GCS Reference Feature 缓存。
- 不自动改变历史不可变 `alignment/<run_id>/result.json` 的内容。

## 1. 必须先核对的现有代码

- `backend/pianokt_backend/alignment/time_guided.py`：`MidiNote`、`read_midi_notes`、`group_chords`、`_row`、`rows_from_operations`。当前 `ref_id` 形式为 `r{reference.track}_n{reference.sequence}`；`group_chords` 使用**相对分组首音**不超过容差的规则，输出 Onset 时间为该组音符时间中位数。**不能改成相邻差值传递分组。**
- `backend/pianokt_backend/contracts.py`：`AlignmentConfig.chord_onset_tolerance_sec = 0.03`。
- `backend/pianokt_backend/alignment/service.py`：解析两份 MIDI → 确定 observed range → `reference_chords` / `performance_chords` → `align_chords` → Alignment DataFrame → 调用 `events.build_attempt_events_data()`。
- `backend/pianokt_backend/alignment/events.py`：目前以 `ref_onset_sec.round(9)` 二次分组，之后在同一函数中计算 KC、错误码和多弹；**这正是要替换的二次分组**。
- `backend/pianokt_backend/alignment/hands.py`：Reference 左右手推断。
- `backend/pianokt_backend/worker.py`：`alignment_run_id`、不可变 `alignment/<run>/result.json`、`_is_current_result` 与 `store.json` 创建写入。
- `backend/pianokt_backend/pipeline/{attempts,training,direct,selection}.py`：数据质量筛选、版本字段、完整 Attempt Dataset 和精简训练导出。
- 既有测试：`backend/tests/test_time_guided_alignment.py`、`test_attempt_dataset.py`、`test_direct_export.py` 及其他相关测试。

## 2. 目标调用链和模块责任

**强制采用以下数据流，不要把 Alignment 移进 `events.py`：**

```text
reference.mid ── read_midi_notes() ── observed range 裁剪 ── group_chords(..., 0.03)
                                                          │
                                     同一份 canonical reference_chords
                                                          │
                       ┌──────────────────────────────────┴────────────────────┐
                       ▼                                                       ▼
              feature_extraction.py                               service.py / Matcher
              KC / Onset 特征输出                        reference_chords + performance_chords
                       │                                           │
                       │                               match / deletion / insertion
                       └─────────────────────┬─────────────────────┘
                                             ▼
                                         events.py
                                   按 ref_id 合并及严格校验
                                 生成 reject_reason / offsets
                                             ▼
                                    result.json.events_data
                                             ▼
                                 pipeline/attempts.py 或 training.py
                                             ▼
                                      Versioned Dataset
```

### `feature_extraction.py`（新增）

- 对外提供清晰的接口，建议 `extract_reference_features(reference_chords, hand_mapping, *, kc_labeler_version=KC_LABELER_VERSION)`。**输入是已分好组的 `Chord`，不是读取文件后另行生成的新分组。**
- 文件内集中定义 `KC_LABELER_VERSION = "1.2.0"` 与六个现有 KC 特征名称/顺序。
- 生成可在 Python 内存中传递的规范中间结构（可以用 `dataclass` / `TypedDict`；本阶段不要求自动保存成 JSON）：
  - Reference 快照作用域内的 Onset 顺序/index 和稳定 `onset_id`（如有需要）；
  - `reference_chord_index`；
  - **该 Onset 的全部原始 `ref_id` 成员**，以及参与目标手部 KC 的成员；
  - Onset 的规范时间（使用共享 `Chord.onset_sec`）；
  - 左/右手目标 pitches 和每个目标 pitch 对应的一个或多个 `ref_id`；
  - 三元手/全局间隔及六维 `kc_vector`。
- 复用当前 `events.py` 中 `_hand_move`、`_interval_bin`、`_move_bin` 的业务定义和白键坐标，不随意调整它们的阈值；**本次语义变化仅来自统一的 30 ms Onset 分组和由此产生的派生值变化**。
- 首个 Onset、左右手无历史时的 `null`、同手上一非空 Onset 作为历史、移动匹配规则，都保持现有语义。
- 从 `hands.py` 获取手部分配结果；让同一份 hand mapping 参与 Feature 构建和后续 Response 关联，避免手部分配再次推断出不同结果。
- 不依赖 Performance、Alignment DataFrame、学生响应、`reject_reason`；不做数据库/GCS IO。

### `events.py`（重构）

- 接口可为 `build_attempt_events_data(reference_features, alignment_df, *, timing_threshold_ms, insertion_attach_window_sec)`。**不再读 Reference MIDI，也不再按时间重新分组，不再自行重算 KC。**
- 用 `ref_id` → `reference_chord_index` / 对应目标音符的映射关联 Alignment 的 `match`、`deletion` 行，取响应分类、time/pitch offsets；`insertion` 没有 `ref_id`，仍按现有规则在容差内分配给最近的规范 Onset，并确保最多关联一次。
- 保持 `events_data` 现有列式数组及 note-level/event-level 形状和 0–4 错误码语义；`event` 错误必须由 note 结果与 `extra_notes` 推导。
- 相同手、相同分组、相同 MIDI pitch 的重复原始音符需要**显式确定规则**：内部保留全部 `ref_id`，不得因为 `drop_duplicates(keep='first')` 随机抛掉一个错误结果。若在保持现有输出形状的前提下无法安全表示冲突，应抛出明确契约错误而不是生成假正确；同时为此补单测。
- 严格检查输入对齐结果；发现结构冲突必须失败，不允许通过忽略不匹配的 `ref_id` 或重新根据时间猜测分组来“修复”。

### `service.py`（轻度调整）

- 在已确定 `observed_end_sec` 后，继续沿用现有方式：**先按已观测范围筛选 Reference Notes，然后进行一次 `group_chords()`**。保留当前结束边界包含容差的行为；不要让 Feature Extraction 单独处理截断。
- 将**完全同一份** `reference_chords` 送给 Matcher 和 Feature Extractor。Performance Chords 继续独立分组，这是正常的，要求统一的是 **Reference 分组**。
- 对 Alignment DataFrame 做强制完整性校验后调用新版 `events.py`。只做模块编排，不自行计算 KC。
- 如果因代码现状需要兼容旧 `build_attempt_events_data(df, reference_path)` 的外部调用，使用清晰的临时 wrapper/迁移方案，但**生产主路径必须使用共享分组**。

## 3. 强制一致性校验（必须实现，不能只写日志）

建议封装 `validate_reference_alignment_consistency(reference_features, alignment_rows)`；调用位置：`service.py` 在构建 `events_data` 前，`events.py` 可进一步校验输出长度/形状。

对每个已观测的 Reference Group 检查：

1. 以 `reference_chord_index` 和 **原始 `ref_id` 的多重集合**（不能先去重）比较：Feature 侧的全部 Reference Notes 必须与 Alignment 的 `match/deletion` 侧一一对应。
2. 不得重复出现任何 `ref_id`，不得缺失，不得跨组，`reference_chord_index` 必须合法。`insertion` 行必须 `ref_id is None`，不能错误记入 Reference 成员。
3. 每个 `ref_id` 的 `ref_pitch`、`ref_track`、`ref_onset_tick`（若可用）和源分组对应记录应一致；不能只比较成员数。
4. 显式区分 **全部 Reference 音轨** 与 **参与 KC 的左右手音轨**：先校验完整组成员，再通过同一份 hand mapping 投影到 `events_data`，不要把未参与手部 KC 的音轨当作“Reference 丢失”。
5. `onset_seconds` 严格递增、每条 `kc_vector` 六维、所有逐事件数组等长，每条目标音符的响应数组与 `pitches` 严格同形；不同源事件绝不可重复消费。
6. 统一 Reference snapshot-relative 时间基准；禁止靠 `round(9)` 或误差匹配替代 ID 校验。
7. 校验错误使用可识别的专用异常（例如 `ReferenceGroupingMismatch`），带 group index / ref_id / expected vs actual 等**非敏感**诊断。错误发生时绝不写出成功的 `result.json` / Dataset；在 Worker 中按照已有错误分类机制处理确定性的内部契约错误，不进行无意义的多次重试，同时不要把内部代码错误误标为用户上传错误。

**重要**：如果某些历史路径确实没有 `ref_id` 或 `reference_chord_index`，不要静默降级到浮点关联；需要对该路径实施显式、可测试的兼容转换，或者清楚地报“旧版本不支持此强校验”。

## 4. 版本标记与不可变结果缓存（非常重要）

唯一新增/统一使用的 KC 版本字段：

```json
{"kc_labeler_version": "1.2.0"}
```

- 从 `feature_extraction.py` 的同一个常量取值，沿着新产物传递。不得在 `attempts.py`、`training.py` 各自重新硬编码 `v1.1` 或 `1.2.0`。
- 在新 `result.json` 的顶层写入 `kc_labeler_version`；`pipeline/attempts.py` 从被选中的实际 result 读取该字段，输出到 Attempt Record，并在 Dataset Manifest / `index_map.json` 中记录同一版本；精简训练导出同样记录所用版本。
- 输出中的 `feature_version = 'v1.1'` 等旧 KC 版本写法需要统一：**新产物不要同时保留两个意义重叠且可能不一致的 KC 版本字段**。若发现下游强依赖旧字段，需要同步修改读取器与测试，并说明向后兼容边界。
- **不得把历史结果（缺少 KC 字段、实际采用旧精确时间分组）自动视作 1.2.0。** 历史读取可以明确标识为 legacy v1.1 或选择排除，但不能伪装；导出默认拒绝把不同 KC 版本静默混在一个 Dataset 中。
- 目前 `alignment_run_id` 用于整个 `result.json` 的 create-only 缓存，且不包含独立 KC 版本。**必须修复旧缓存复用问题**。本次采用最低风险、与当前 GCS/DB 指针兼容的方案：在现有 `alignment_run_id(...)` 的稳定身份输入中加入 `KC_LABELER_VERSION`，并在 `_is_current_result()` 同时验证结果的 `kc_labeler_version`。这样 1.2.0 的结果生成新 run ID，不覆盖旧文件。相应调整所有生成 run ID 的入口、Worker 重试逻辑和测试。**代价是版本切换时需要重新执行一次 Alignment；这是当前组合式不可变 `result.json` 的权衡，不要声称这一步已经做到免 Alignment 重标注。**
- 不在本次引入第二种线上 Artifact 布局。未来若要“只改 KC、不重跑 Alignment”，再单独做基于历史 `result.json.notes`（最终校准后的 Alignment 行）+ 原始 Reference 快照的离线 relabel 命令，并把衍生结果存储在独立 create-only 路径，避免破坏现有 READY 指针契约。**这个离线能力本次可以不实现**，但新函数必须支持传入已存在的 Alignment DataFrame，以便未来直接复用。

## 5. 测试 / 验收（全部需跑通）

请新增必要的单测和集成测试，不得只修改旧测试的期望值使其变绿。

### 分组规则与身份

- 0、20、40 ms 三个起音应分为 `[0,20]` 和 `[40]`，不是全部合并；检查共享 `group_chords()` 的 **group-first anchor** 语义。
- 恰好相差 30 ms 应按当前 `<= 0.03` 规则归组；覆盖同 tick、多音轨、同 pitch 跨手、多次同 pitch。
- `Chord.onset_sec` 取成员 onset 中位数；输出 KC Onset 和 Alignment Group 索引一致，且所有 `ref_id` 正确对应。
- 修改一个 `ref_id`、遗漏一行、重复一行、调换 `reference_chord_index`、错配 pitch/track，均应触发明确异常，且不生成成功数据。

### 功能语义

- 仍维持六维 KC 名称和顺序、移动/间隔分箱、左右手历史和首 Onset `null` 行为。
- 分组带来的 KC 变化要用明确的 expected fixture 覆盖；不要求近同时起音场景和 v1.1 完全一致。
- `match` / `deletion` / `insertion`、错音、漏音、timing_error、extra_notes 和 event-level 正误派生正确；多弹不重复计算。
- 仅导出 observed range 中的目标 Onsets；暂停/退出导致的后续未尝试音符不能变成漏弹；结束边界 30 ms 容差覆盖。
- 变速 MIDI、`reference_time_origin_sec` 非零、坏的 transport telemetry / 非单调录制等现有回归不退化。

### 版本与导出

- 新 `result.json`、`attempts.jsonl`、Manifest、`index_map.json`、training JSON 的 KC 版本都是 `"1.2.0"`（具体字段层级与当前结构协调）；不能残留 hardcoded `feature_version: v1.1`。
- 缺少 KC 版本的历史结果不能自动升级；不同版本混合默认有明确报错或有明确过滤策略。
- 与旧 run 相同的 MIDI/telemetry/Matcher，变更到新 KC 版本后得到不同不可变 `result_key`，不会误用旧 `result.json`；重试同一新 run 时仍幂等。
- `export-attempts` 的完整 note-level reject reasons 不丢失；`export-training` 继续产出精简 event-level 样本。

运行并记录至少以下测试（按工程实际配置调整命令）：

```bash
cd backend
pytest -q tests/test_time_guided_alignment.py tests/test_attempt_dataset.py tests/test_direct_export.py
pytest -q
```

## 6. 实施顺序（按顺序完成）

1. 阅读上述文件，列出当前主调用点和会受影响的测试；确认生产和本地 `align` 入口。
2. 建立 `feature_extraction.py` 和规范 ReferenceFeatures 数据契约，共用一次 `group_chords()`；统一 `ref_id` 生成，避免重复写身份规则。
3. 在 `service.py` 将共享的 `reference_chords` 同时传递给 Feature Extraction 与 Matcher；处理 observed range、hand mapping，并确保时间基准统一。
4. 重构 `events.py` 为按 `ref_id` 合并结果和构建 Response；增加严格一致性 validator；移除原有 `ref_onset_sec.round(9)` 二次分组。
5. 完成 `kc_labeler_version = "1.2.0"` 的统一传播，调整 `alignment_run_id` / Worker 缓存校验 / exporter，保护历史结果。
6. 新增分组、identity、错误码、边界、缓存版本、数据集导出测试，跑完整后端测试。
7. 更新必要的 schema / data pipeline 文档，说明 **30 ms Onset 定义相对 v1.1 的语义变化**，以及当前改造尚未实现免 Alignment 的历史重标注。

## 7. Codex 完成后的交付报告

最终直接给出：

1. 实际改动的文件列表及每个模块的新职责；
2. 关键调用链（从 Reference/Performance 到 Dataset）；
3. 30 ms 分组和 `ref_id` 校验如何保证一致性；
4. `kc_labeler_version = "1.2.0"` 的出现位置与历史缓存处理方式；
5. 运行的测试命令、通过/失败结果，以及尚存的边界风险；
6. 明确说明**未执行生产部署、未覆写旧 GCS 对象、未重新标注历史数据**（除非实际用户另外授权并且确实执行）。

**验收底线**：若两侧 Reference 分组或身份不一致，必须**失败并暴露错误**，不能生成看似有效的 KC/KT Dataset；若只有 KC 版本发生变化，也不能不加检查地复用旧版 `result.json`。
