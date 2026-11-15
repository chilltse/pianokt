# PianoKT：统一 Alignment、Reference Grouping 与 KC Feature Extraction 的版本化架构

请基于当前 PianoKT 仓库，完整实现轻量级、模块化、版本化的 Alignment 和 Reference Grouping，并统一前端实时演奏反馈、后端 Alignment、Dataset Event Grouping、KC Feature Extraction 和 Recommendation Candidate Extraction 的相关标准。

首先检查当前工作分支、最新代码、现有模块和完整调用链。充分复用已有实现，避免过度工程化、重复代码及无关重构。

本次任务需要实际完成代码修改、测试和文档，而不只是提供一个设计方案。

## 1. 总体架构与版本管理

将系统划分为三个相互独立、可版本化的模块：

- `reference_grouping_version = "1.0.0"`
- `alignment_version = "1.0.0"`
- `kc_labeler_version = "1.2.0"`

其中：

- Reference Grouping 定义 Reference MIDI 中哪些音符构成一个 onset event/question。
- Alignment 定义如何将学生演奏与 Reference Event 进行匹配，以及如何判断正确、错误、漏音和额外音符。
- KC Feature Extraction 定义如何根据 Reference Event 提取 KC Vector。

Alignment 和 KC Feature Extraction 共同依赖 Reference Grouping，但不应相互依赖对方的版本。

逻辑依赖关系：

```text
Reference MIDI
      |
Reference Grouping v1.0.0
      |
Canonical Reference Events
      |
      +--------------------------+
      |                          |
Alignment v1.0.0         KC Labeler v1.2.0
      |                          |
Response Labels              KC Vectors
      |                          |
      +--------------------------+
                  |
              events_data
                  |
             KT Dataset
```

Standalone Reference Extraction 和 Recommendation Candidate Extraction 只依赖 Reference Grouping 与 KC Labeler，不应依赖具体的 Alignment Policy Version。

### 建议目录结构

```text
backend/pianokt_backend/alignment/
├── __init__.py
├── service.py
├── time_guided.py
├── events.py
├── labels.py
├── reference_grouping/
│   ├── __init__.py
│   ├── registry.py
│   ├── grouping.py
│   └── versions/
│       └── v1_0_0.json
├── alignment_policies/
│   ├── __init__.py
│   ├── registry.py
│   └── versions/
│       └── v1_0_0.json
└── feature_extraction/
    ├── __init__.py
    ├── registry.py
    ├── reference.py
    ├── types.py
    └── versions/
        └── v1_2_0.py
```

尽量沿用现有 `feature_extraction` 的版本注册方式。

对于仅涉及参数变化的版本，使用不可变 JSON 配置，不复制整个 Python 算法模块。

如果未来算法本身发生语义变化，则允许注册对应版本的算法实现，以保证历史版本可复现。

## 2. 独立的 Reference Grouping Policy

新增：

`backend/pianokt_backend/alignment/reference_grouping/versions/v1_0_0.json`

内容：

```json
{
  "version": "1.0.0",
  "time_basis": "song_time",
  "grouping_method": "first_note_anchor",
  "grouping_representative": "median",
  "onset_grouping_ms": 30
}
```

这是 Reference Onset Grouping 的唯一权威配置。

不要在 Alignment Policy、KC Labeler 或其他模块中重复定义30ms阈值。

### Grouping Algorithm

Reference Grouping 必须遵循以下规则：

1. 按原始 onset 时间排序。
2. 以每组第一个音符的 onset 为固定锚点。
3. 与该锚点时间差不超过30ms的后续音符归入同一组。
4. 禁止基于相邻音符之间的距离进行链式聚合。
5. 使用组内所有音符 onset 的中位数作为 Reference Event 的代表时间。
6. 保留原始 note onset、pitch、track、channel、onset tick、sequence 和 ref_id。
7. 不得修改原始 MIDI 中的音符时间。
8. 所有时间窗口包含边界，并合理处理浮点精度问题。

例如：

```text
C4: 0ms
E4: 25ms
G4: 50ms
B4: 75ms
```

正确结果：

```text
Event 1: C4, E4
Event 2: G4, B4
```

而不是将四个音符链式聚合为一个事件。

另一个例子：

```text
C4: 0ms
E4: 10ms
G4: 25ms
```

正确结果：

```text
Event 1:
pitches = [60, 64, 67]
representative_onset = 10ms
```

三个音符构成一个 KT Question。

### 公共接口

提供轻量级、明确的公共接口，例如：

```python
group_reference_onsets(
    notes,
    reference_grouping_version=None,
)
```

具体类型和函数签名可以根据现有项目结构合理调整。

优先复用 `time_guided.py` 中现有的固定首音锚点 `group_chords()` 实现，将通用分组逻辑放到合理的公共位置。

注意避免循环 import。

不要为 Reference Grouping 创建独立服务或额外网络 API。

## 3. 独立的 Alignment Policy

新增：

`backend/pianokt_backend/alignment/alignment_policies/versions/v1_0_0.json`

内容：

```json
{
  "version": "1.0.0",
  "matcher": "time-guided-chord-monotonic",
  "time_basis": "song_time",
  "performance_grouping_method": "first_note_anchor",
  "performance_grouping_representative": "median",
  "performance_chord_grouping_ms": 50,
  "match_window_ms": 1000,
  "perfect_threshold_ms": 50,
  "correct_threshold_ms": 200,
  "wrong_pitch_window_ms": 300,
  "extra_note_attachment_ms": 300
}
```

注意：

- Reference Grouping 的30ms不应出现在此文件中。
- Alignment Policy 与 Reference Grouping Version 独立选择。
- 默认运行组合为 Alignment v1.0.0 + Reference Grouping v1.0.0。
- Alignment 可以声明支持的 Reference Grouping Versions，但不得复制其分组参数。
- 不允许使用不兼容或未经支持的版本组合并静默回退。

最终参数：

| Parameter | Value | Owner |
|---|---:|---|
| Reference onset grouping | 30ms | Reference Grouping |
| Performance chord grouping | 50ms | Alignment Policy |
| Match window | 1000ms | Alignment Policy |
| Perfect threshold | 50ms | Alignment Policy |
| Good / Correct threshold | 200ms | Alignment Policy |
| Wrong pitch window | 300ms | Alignment Policy |
| Extra note attachment | 300ms | Alignment Policy |

## 4. 统一版本解析与配置加载

复用现有的 `AlignmentConfig` 和 KC Version Registry，不引入复杂配置框架。

为 Reference Grouping 和 Alignment 分别提供类似以下的接口：

```python
resolve_reference_grouping_version(version=None)

load_reference_grouping_policy(version=None)

resolve_alignment_version(version=None)

load_alignment_policy(version=None)
```

要求：

- 支持明确指定版本。
- 存在明确的默认版本。
- 未知版本直接报错。
- 已发布的版本文件保持不可变。
- 运行时配置必须能够追溯至具体版本。
- 禁止在不同模块中分别硬编码相同阈值。
- 检查配置有效性，包括非负时间窗口、Perfect threshold 不超过 Correct threshold 等。
- 在 `pyproject.toml` 中正确配置 JSON package data。
- 确保本地运行、wheel 安装和 Docker 部署都可以正确加载配置。
- 不增加新的第三方依赖。

对于未来的版本升级，凡是可能改变 Question、KC Vector、Alignment Result 或 response labels 的修改，都必须使用新的版本标识。

不能在原版本号下静默改变数据语义。

## 5. 更新 Backend Alignment

重点检查：

```text
contracts.py
alignment/service.py
alignment/time_guided.py
alignment/events.py
alignment/labels.py
alignment/serialization.py
worker.py
```

保留现有 time-guided、chord-aware、monotonic matching 架构。

### Reference Grouping

Reference MIDI 必须通过统一的 `group_reference_onsets()` 生成 canonical Reference Events。

Alignment 不再自行定义 Reference Grouping 参数。

### Performance Grouping

Performance 使用独立的50ms窗口。

同样采用固定首音锚点、中位数代表时间。

Reference Grouping 和 Performance Grouping 不得继续共用同一个 tolerance 参数。

Performance 的分组结果不得改变 Reference Question 的定义。

### Match Window

Match Window = 1000ms。

该窗口用于 chord-level matching candidate search，不用于判断演奏是否正确。

保留现有：

- Monotonic matching。
- Exact pitch priority。
- 每个 Reference Note 和 Performance Note 最多被消费一次。
- Chord-level 一对一匹配。
- Dynamic programming 及现有资源限制和 fallback 逻辑。

例如，一个音符偏差500ms，仍可能成功匹配，但不能被标记为 Correct。

### Correct Threshold

对于已经成功匹配且 pitch 正确的音符：

```text
|timing deviation| <= 200ms:
Correct

|timing deviation| > 200ms:
Timing Error
```

判断 timing error 时，应使用目标音符自身的原始 Reference Onset，而不是用和弦的 median onset 替代所有音符的实际 onset。

Chord median 用于分组代表时间和 chord-level matching。

### Wrong Pitch Window

Wrong Pitch Window = 300ms。

检查 `_chord_match_cost()` 和 `_pair_chord_notes()`。

现有代码可能只在两个 chord 完全没有 pitch overlap 时严格限制 wrong pitch window。

当存在部分 pitch overlap 时，后续不同 pitch 的音符仍可能被直接配对。

需要修复：

- Zero-overlap chord match 必须满足300ms窗口。
- Note-level wrong pitch substitution 同样必须满足300ms窗口。
- 超过窗口的不同 pitch 音符不得强制配对。
- 未匹配的 Reference Note 保留为 deletion。
- 未匹配的 Performance Note 保留为 insertion。

### Extra Note Attachment

Extra Note Attachment Window = 300ms。

Insertion 只能附着到最近且时间偏差不超过300ms的 canonical Reference Event。

超过300ms时：

- 不附着到任何 Reference Event。
- 保留原始 insertion alignment record。
- 不得静默丢弃额外音符。
- 不得将其强行编码为某个 Question 的错误。

保留原有确定性的 tie-breaking 规则，或者明确补充合理且可复现的规则。

## 6. 统一前端实时反馈

重点检查：

`src/features/player/player.ts`

以及其他实际使用 Perfect、Good、Miss、Error 的 scoring、timeline、visualization 和 challenge 模块。

前端直接读取同一个版本化 Alignment Policy JSON。

不要分别在前端和后端定义阈值。

前端在构建时加载 JSON，不增加运行时网络请求。

标准：

```text
Perfect:
Pitch correct AND |timing deviation| <= 50ms

Good:
Pitch correct AND 50ms < |timing deviation| <= 200ms
```

超过200ms或 pitch 错误的音符不得计为 Perfect 或 Good。

重点检查：

- `PERFECT_RANGE`
- `GOOD_RANGE`
- `clearMissedNotes_()`
- `processScoreData()`
- `getUpcomingNotes()`
- `calcDiff()`
- `lateNotes`
- Early hit
- Late hit
- Repeated pitches
- Active hands
- Transpose
- Wait mode
- Playback speed

保留现有 Perfect、Good、Miss、Error 的 UI 和评分权重，除非为了修复一致性问题必须局部修改。

### 统一时间基准

当前前端 `calcDiff()` 可能通过除以 `bpmModifier` 将 song-time deviation 转换为现实时间偏差，而后端使用 player-song-time。

本次统一为 song-time milliseconds：

```typescript
calcDiff(to: number, from: number) {
  return (to - from) * 1000
}
```

检查所有 `calcDiff()` 调用者，确保修改不会影响无关功能。

在0.5x、1x、2x playback speed 下验证前后端采用相同的时间基准。

注意：这里的200ms是曲谱时间，不一定等于现实世界的200ms。

如果前端需要 Reference Event Grouping，应读取同一个 Reference Grouping Policy，并通过轻量级 TypeScript 实现相同的规则，使用跨语言 golden fixtures 验证一致性。

不要为了实现相同的 Reference Grouping 而增加后端网络请求。

前端实时反馈是 online note-level scoring，后端是 offline chord-level alignment。共享规则和阈值不代表两者在所有复杂竞争匹配场景中必然产生相同结果。

后端仍然是科研数据与最终 Alignment Result 的权威来源。

## 7. 统一 Dataset Event Grouping

检查：

```text
alignment/events.py
alignment/prototype.py
alignment/feature_extraction/
pipeline/
```

较新的代码已经具备 `ReferenceFeatures`、`ref_id`、`reference_chord_index` 等 canonical Reference identity 机制，应充分复用。

特别检查旧代码：

```python
ref_side["_event_time"] = ref_side[ref_onset_col].round(3)
```

如果此逻辑属于 legacy path，则不得重新引入主流程。

如果仍存在活跃调用，则迁移至统一的 Reference Grouping。

Dataset Event 必须由 canonical Reference Event 定义，而不是根据 Performance Grouping 或独立的浮点时间分组定义。

保持：

- 一个 canonical Reference Event 对应一个 KT Question。
- 每个 Question 包含对应的左右手 pitch 集合。
- 每个目标音符保留独立 response label。
- Event-level response 由目标音符和附着的 Extra Notes 决定。
- Reference Event identity 在 Alignment、KC Extraction 与 Dataset 中保持一致。

保持原有 note-level reject_reason：

```text
0 = Correct
1 = Missing
2 = Wrong Pitch
3 = Timing Error
4 = Wrong Pitch + Timing Error
```

保持原有 event-level response：

- 所有目标音符正确且不存在附着 Extra Notes：Correct。
- 存在任意目标音符错误、漏音或附着 Extra Notes：Incorrect。

Perfect 和 Good 都对应 note-level `reject_reason = 0`。

不改变 KT 的二分类任务。

## 8. 保持 KC Labeler 独立

保留现有：

`kc_labeler_version = "1.2.0"`

不修改六维 KC Feature 的数学定义：

```text
has_black_key
simultaneous_note_count
both_hands
onset_interval_bin_cs
change_count
mean_move_bin
```

KC Labeler 只能依赖 canonical Reference Events 及其必要的 Reference 上下文，不应依赖：

- Performance MIDI。
- 学生作答结果。
- Match Window。
- Correct Threshold。
- Perfect Threshold。
- Wrong Pitch Window。
- Alignment Policy Version。

KC Feature Extraction 的完整语义身份由以下组合决定：

```text
reference_grouping_version
kc_labeler_version
reference MIDI identity
reference context
```

仅修改 Alignment Policy，不应改变同一 Reference 的 KC Vector。

例如，从200ms Correct Threshold 改为250ms，只需要重新生成 Alignment 和 response labels，不需要重新生成曲库 KC 特征。

如果 Reference Grouping 从30ms变为40ms，则即使 KC Labeler Version 仍为1.2.0，也可能需要重新生成所有受影响的 Reference Features、Dataset Events 和推荐候选特征。

## 9. Standalone Reference 与推荐曲库

检查：

```text
alignment/feature_extraction/reference.py
pykt_integration.py
Standalone Reference Extraction CLI
Recommendation Candidate Extraction
曲库特征缓存与推荐候选生成
```

Standalone Reference Extraction 必须支持指定：

```text
reference_grouping_version
kc_labeler_version
```

不应要求指定 Alignment Policy Version。

建议参考现有 CLI，实现类似：

```bash
pianokt-data extract-reference \
  --reference-grouping-version 1.0.0 \
  --kc-labeler-version 1.2.0
```

具体参数根据实际 CLI 结构调整，保持原有命令兼容性。

Standalone Reference Artifact 应记录：

```json
{
  "reference_grouping_version": "1.0.0",
  "kc_labeler_version": "1.2.0"
}
```

以及现有的 MIDI hash、Reference Event IDs、KC Feature Names、Reference Context 等 provenance 信息。

检查推荐候选缓存键。

缓存身份至少包含：

```text
reference MIDI hash
reference_grouping_version
kc_labeler_version
```

如果结果还依赖 segment bounds、hand mapping、transpose 或其他上下文，则必须纳入相应的缓存身份。

Alignment Policy Version 不应被加入纯 Reference Feature Cache Key。

### 曲库与 Segment 一致性

特别检查：

- 完整曲目的 Reference Extraction。
- Segment Candidate Extraction。
- Challenge Reference Snapshot。
- Assigned Range。
- Observed Range。
- 提前退出。
- 左右手选择。
- Transpose。
- MIDI serialization。

必须明确区分完整曲目、segment 和学生实际练习时的 Reference 上下文。

避免因为先裁剪再分组而导致一个 canonical chord 被拆成不同 Question。

在可获得完整 Reference 的情况下，优先先确定 canonical groups，再按约定选择完整事件。

对于被 snapshot 边界截断、无法恢复完整分组的情况，制定明确规则并记录 provenance，必要时排除，不得假装其与完整 Reference 完全一致。

尤其需要检查 KC Feature 中 previous onset、change count、mean move 等依赖历史 Reference Context 的特征，避免在完整曲目和 segment 起点使用不同上下文却被认为完全等价。

## 10. 版本溯源与数据兼容性

Alignment Result 应明确记录：

```json
{
  "alignment_version": "1.0.0",
  "reference_grouping_version": "1.0.0",
  "kc_labeler_version": "1.2.0"
}
```

Standalone Reference Artifact 仅记录：

```json
{
  "reference_grouping_version": "1.0.0",
  "kc_labeler_version": "1.2.0"
}
```

检查以下完整链路：

- Frontend Recording Metadata。
- Attempt Metadata。
- Worker。
- Alignment result.json。
- Matching artifacts。
- Bronze/Silver/Gold。
- Training Dataset Export。
- Standalone Reference artifacts。
- Recommendation Candidate Features。
- KT Model Compatibility Verification。

前端应该记录实际使用的 Alignment Version 和 Reference Grouping Version（如果前端执行 Reference Grouping）。

Worker 必须明确处理旧前端版本、旧排队 attempt 和不兼容版本组合。

不得静默使用新的默认版本覆盖已经明确指定的历史版本。

### Alignment Run ID

检查现有 `alignment_run_id()`。

确保完整身份包含：

```text
attempt identity
reference MIDI hash
performance MIDI hash
alignment_version
reference_grouping_version
kc_labeler_version
resolved policy configuration
matcher implementation identity
existing transport and attempt context
```

任意能够改变最终 Alignment Result 的版本变化，都应产生不同的 immutable alignment run identity。

不要破坏当前 Worker crash recovery、GCS immutable object 和 idempotency 机制。

历史 `time-guided-chord-monotonic-v3` 数据保留其原始版本身份。

新的 `1.0.0` 是正式语义化版本体系的起点，不代表历史 v3 的简单重命名。

如果旧版本不能被当前代码精确重现，必须明确报告，不得通过重新标注版本假装兼容。

### KT 模型兼容性

检查 PyKT Integration 和 Training Export 中现有的 compatibility contract。

模型训练元数据应能够追溯训练数据所使用的：

```text
alignment_version
reference_grouping_version
kc_labeler_version
```

对于纯候选 Reference Feature 的语义兼容性，重点匹配 Reference Grouping Version 和 KC Labeler Version。

Alignment Version 则用于训练 response label 与学生历史作答语义的兼容性判断。

不要因为仅修改 Perfect Threshold 等不影响 Reference Features 的参数，就无条件使所有曲库 KC 缓存失效。

禁止在未明确声明兼容性或实验目的的情况下混合不同标签语义的训练数据。

## 11. 测试与科学验证

新增必要的 Python 和 TypeScript 单元测试、集成测试以及跨语言共享 golden fixtures。

至少覆盖：

### Reference Grouping

- 30ms 边界。
- 31ms 超出窗口。
- 固定首音锚点。
- 非链式聚合。
- 中位数代表时间。
- 单音、双手和弦、重复音高。
- 原始 note identity 保留。
- Alignment 和 KC Extraction 使用相同 canonical grouping。
- Segment 和提前退出边界。

### Alignment

- Performance 50ms grouping。
- Match Window 1000ms 边界。
- Perfect threshold 50ms 边界。
- Correct threshold 200ms 边界。
- Wrong Pitch Window 300ms 边界。
- Extra Note Attachment 300ms 边界。
- 正负时间偏差。
- Missing、Wrong Pitch、Timing Error、Extra Note。
- Partial pitch overlap。
- 和弦中部分音符未弹奏。
- 和弦中部分音符明显过早或过晚。
- 重复 pitch。
- 密集音符。
- 不重复消费音符。
- 保持单调匹配。

### Frontend

- 前后端加载相同 Alignment Policy。
- Perfect / Good 的精确边界。
- Early hit / late hit。
- Miss expiry。
- 重复音高。
- 双手选择。
- Transpose。
- Seek。
- Wait mode。
- 0.5x、1x 和2x playback speed。

### 版本独立性

必须专门验证：

1. 只改变 Alignment Policy，不改变 Reference Grouping 和 KC Labeler 时，Reference Features 和 KC Vectors 完全一致，纯 Reference Feature Cache 不失效。

2. 改变 Reference Grouping Version 时，Alignment 与 KC Extraction 均使用新的 canonical Reference Events，并正确区分缓存、数据集和运行身份。

3. 只改变 KC Labeler Version 时，不改变底层 Reference Grouping 和音符匹配结果。

4. 不同语义版本的 Alignment Result 不会使用相同的 alignment_run_id。

5. 未知版本和不兼容版本组合明确报错。

6. 历史 GCS Alignment Result 不被覆盖。

7. Python package、Docker image 和前端构建可以正确加载各自需要的 JSON 文件。

### Synthetic Error-Injection Test

使用已有 MIDI fixtures 构建小规模 synthetic error-injection 测试。

人为注入：

- Timing shifts。
- Missing notes。
- Wrong pitches。
- Extra notes。
- Chord onset dispersion。

对比旧版本与新版本的匹配和作答结果。

统计：

- Note matching accuracy（基于已知注入 ground truth）。
- Missing notes。
- Wrong pitch。
- Timing errors。
- Extra notes。
- Event-level correct rate。
- Reference Event Count。
- KC Vector consistency。

真实数据不可用时，应明确区分 synthetic validation 与真实演奏验证，不得夸大结论。

运行现有完整测试套件，包括适用的：

```bash
python -m pytest
```

```bash
bun test
bun run check-types
bun run build
```

根据实际项目环境调整命令。

禁止为了让测试通过而随意删除旧测试或无依据地放宽断言。

## 12. 工程约束

本次改动必须保持轻量级。

- 不引入新的微服务。
- 不引入新的第三方依赖。
- 不进行无关重构。
- 不重新设计整个 Alignment Algorithm。
- 不重写 Data Pipeline。
- 不修改 KC 数学定义。
- 不修改 KT 模型架构、模型权重和词表。
- 不修改推荐算法。
- 不覆盖历史数据。
- 不自动重新处理生产数据。
- 不进行非必要的数据库 Schema Migration。
- 优先复用现有 metadata、config 和 provenance 字段。
- 不修改 Supabase 生产数据。
- 不部署到生产环境。
- 保护用户尚未提交的代码。

如果发现其他问题，区分本次必须修复的一致性问题和与本次任务无关的技术债务，不要无限扩大修改范围。

## 13. 文档与最终交付

新增：

`docs/tse/alignment_and_reference_grouping.md`

文档需要说明：

- 三个模块的职责及依赖关系。
- 三个独立版本的定义。
- 目录结构。
- 所有阈值的唯一配置来源。
- Reference Grouping 与 Performance Grouping 的区别。
- 前后端共享参数的方式。
- 如何选择指定版本。
- 如何新增一个版本。
- 哪些情况下需要重新生成 Alignment Result。
- 哪些情况下需要重新生成曲库 KC Features。
- 哪些情况下需要重新生成训练数据。
- KT Model Compatibility。
- 历史版本兼容性。
- Online Feedback 和 Offline Alignment 的区别。
- 已知限制与验证结果。

完成后提供简洁的实施报告，包含：

1. 实际修改的文件。
2. 修改前后的架构与行为差异。
3. 三个模块之间的最终依赖关系。
4. 实际通过、失败及未执行的测试。
5. 版本兼容性与历史数据影响。
6. 使用方法及仍然存在的问题。

本次任务最重要的设计原则是：

Reference Grouping 是 Alignment 和 KC Labeler 共同依赖的 canonical upstream module。

Alignment 负责学生作答与响应标签。

KC Labeler 负责 Reference 本身的特征表达。

三个模块独立版本化，通过统一的 Reference Event Identity 联系起来。

始终优先保证科研数据的语义一致性、可复现性和工程简洁性。