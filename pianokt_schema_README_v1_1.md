# PianoKT Attempt Schema：外层 v1.2，事件 v1.1

**用途**：以一次学生练习（attempt）为外层记录，用多个 onset-level events 表示同一练习的目标乐谱、结构化 KC 特征及实际演奏结果。支持 event-level Binary KT、note-level KT 和 multiclass-error KT。  
**配套示例**：`pianokt_attempt_example_v1_1.json`。  
**状态**：当前 `alignment/events.py` 与 `pipeline/attempts.py` 已采用此结构；错误码、KC 桶与默认 150 ms 阈值是本工程研究约定，未经真人评估验证，不代表外部产品标准。本文按 2026-11-03 代码整理，完整链路见 [架构总览](docs/ARCHITECTURE_ZH.md)。

## 1. 数据组织与索引

一条顶层 JSON = **一个学生的一次练习记录**。`events_data` 是列式结构，各个事件字段数组按**相同的索引 `i`**一一对齐，表示第 `i` 个目标 onset event。

```text
Attempt
├─ user_id / song_id / exercise_id / visit_index / attempt_index / ...
└─ events_data
   ├─ onset_seconds[i]                   乐谱目标 onset 时间
   ├─ pitches[i]                        [[左手目标MIDI音高...], [右手目标MIDI音高...]]
   ├─ intervals_seconds[i]              [距左手上次起音, 距右手上次起音, 距全局上次起音]
   ├─ kc_vectors[i]                     六维题目特征（两维离散化）
   ├─ reject_reason.event[i]            整个 onset 的二元错误标记
   ├─ reject_reason.note[i]             与 pitches[i] 完全同形状的逐音符错误码
   ├─ timing_offset_ms[i]               与 pitches[i] 同形状
   ├─ pitch_offset_semitones[i]         与 pitches[i] 同形状
   └─ extra_notes[i]                    该 onset 对应的未匹配多弹MIDI音高
```

`kc_feature_names` 是**唯一存一次**的六维特征名称及顺序声明；`kc_vectors[i]` 是数值数组，非字典。虽然字段名使用复数 `kc_vectors`，但每一个 onset 始终对应**恰好一个** `kc_vector`。

**索引不变量**：所有逐事件数组长度相同，且 `pitches[i][h]`、`reject_reason.note[i][h]`、`timing_offset_ms[i][h]`、`pitch_offset_semitones[i][h]` 的长度相同（`h=0` 左手，`h=1` 右手）。

## 2. 顶层 Attempt 字段

| 字段 | 类型 | 含义与单位 |
|---|---|---|
| `user_id` | string | 匿名化学生标识；同一学生跨练习保持稳定 |
| `song_id` | string | 所属歌曲标识；不同歌曲不应重复使用同一标识 |
| `exercise_id` | string | 实际练习项目标识，例如整首、分段或某一版本 |
| `days_since_signup` | number | 自用户注册以来的天数；小数表示不足一天的时间 |
| `song_type` | string | 歌曲/练习来源类型，例如 `pianokt_song` |
| `visit_index` | integer | 同一学生的网页访问顺序，从 1 开始；同一次访问内多次演奏共用该编号 |
| `attempt_index` | integer | 同一学生的演奏顺序，从 1 开始、跨 visit 连续；按实际开始时间的 UTC 值排序 |
| `play_mode` | string | 练习模式，例如 `challenge` |
| `chords_evaluated` | integer | 被评估的和弦数量；本示例将 2 个或以上目标音的 onset 视为 chord |
| `chords_successful` | integer | 上述和弦中，event-level 判为正确的数量 |
| `notes_evaluated` | integer | 实际纳入评估的目标音符数量，不包含 `extra_notes` |
| `notes_successful` | integer | `reject_reason.note == 0` 的目标音符数量 |
| `time_playing` | number | 本次练习的实际时长，单位秒 |
| `is_played_in_full` | string | 保持原来源约定：`"t"` 为完成，`"f"` 为未完整演奏（可在预处理时转 boolean） |
| `exit_status` | string | 退出或结束状态，例如 `quit` |
| `exercise_part_index` | integer | 练习所对应的片段序号；示例为 0 |
| `events_data` | object | 本次 attempt 的逐 onset 事件及响应信息 |

**顺序与关联（v1.2 更新）**：示例文件名保留 v1_1；事件结构和 KC 规则仍为 v1.1，外层用 `visit_index` 和 `attempt_index` 代替旧的 `session_index`。两个编号仅在同一个 `dataset_version` 内有效，均按学生独立编号。先对截止时间前已收到的完整访问/演奏历史编号，再过滤训练质量，故可有缺号。排序用 UTC 时间值而不是原始 ISO 字符串；并列用稳定 ID 及访问内采集序号解决。补传历史后新版本编号可能变化。

网页访问、attempt 的 ID 和开始/结束时间分别保存在 `student_visits`、`student_attempts`；暂停/继续/退出节点在 `student_attempt_events`。数据集本身不重复时间戳或内部 ID。导出附带 `index_map.json`，通过 `(dataset_version, user_id, attempt_index)` 关联 `attempt_id`、`visit_id`、`score_version`、`alignment_run_id`。示例编号和时间特征仅为说明值，不代表真实学生历史。

示例使用 `is_played_in_full: "f"` 和 `exit_status: "quit"`，两者语义一致；部分完成只记录实际已评估的 events，不把尚未尝试的后续乐谱事件自动记成漏音。

## 3. Events 基础信息

### `onset_seconds[i]`

从**目标乐谱（Score MIDI）**推导的 onset 时间，单位秒；按时间从小到大排序。它不是学生真实弹奏的 onset。对于同一个练习版本，理论上所有学生的这组目标 onset 相同。若乐谱包含速度变化，需要按 MIDI tempo map 正确换算秒数。

### `pitches[i]`

结构始终为：`[[左手音高...], [右手音高...]]`。音高是 MIDI 整数，**全部是目标音高**，不是学生实际弹奏的音高；例如 `[[50,53],[62,65]]`。没有当前 onset 的一只手用 `[]`，不能省略那一侧。在本规范中，同一手同时刻相同 MIDI 音高去重；不同手的相同 MIDI 音高视为不同的 `(hand,pitch)`。建议手内按音高升序存储以保证匹配/错误数组顺序稳定。

### `intervals_seconds[i]`

始终是三元数组：`[left_interval, right_interval, global_interval]`。

- `left_interval`：本事件左手有起音时，当前 onset 与**左手上次有起音**的乐谱时间差；当前左手无起音或是左手第一次起音则 `null`。
- `right_interval`：与左手规则对称。
- `global_interval`：当前 onset 与**上一个全局 onset** 的乐谱时间差；全曲或所分析片段的第一条事件为 `null`，**不把开头静默时间计入**。

例如 `onset_seconds = [0, 0.5, 1.0, 1.75, 2.0, 2.5]`，当右手在 1.0 秒演奏后直到 2.0 秒才再次出现时，即使中间 1.75 秒左手有起音，2.0 秒右手间隔仍为 1.0 秒，而全局间隔为 0.25 秒。

## 4. KC Vector：定义、匹配算法与离散化

固定长度为 6，**每条 onset 对应恰好一个 KC 向量**：

```json
"kc_feature_names": [
  "has_black_key",
  "simultaneous_note_count",
  "both_hands",
  "onset_interval_bin_cs",
  "change_count",
  "mean_move_bin"
]
```

记事件 `i` 的左右手当前目标音集合为 `C_{i,L}` 和 `C_{i,R}`，上一**同一只手**的起音集合为 `P_{i,L}` 和 `P_{i,R}`。第一次出现某只手时，其上一集合视为 `∅`；当前没有起音的手不参与本事件的匹配，也不更新历史集合。

| 索引 | KC 特征 | 精确定义 | 类型/范围 |
|---|---|---|---|
| 0 | `has_black_key` | 当前任意目标音 `p % 12 ∈ {1,3,6,8,10}` | boolean |
| 1 | `simultaneous_note_count` | 当前 onset 的不同 `(hand, MIDI pitch)` 数量；不包括以前 onset 的延音 | 原始整数，未分箱 |
| 2 | `both_hands` | 左右手在当前 onset 都有目标起音 | boolean |
| 3 | `onset_interval_bin_cs` | 当前全局 onset 到上一全局 onset 的时间差离散化；**数值标签是厘秒 cs** | `10/30/50/70/90/null` |
| 4 | `change_count` | `Σ_{h:当前活跃} |C_{i,h} \ P_{i,h}|`；仅新增音高计数，消失音不计 | 原始整数，未分箱 |
| 5 | `mean_move_bin` | 先按下述最佳音高匹配求平均白键位置移动距离 `d`，再分箱 | `0/1/2/3/4/null` |

### 4.1 `onset_interval_bin_cs`：五个中心值

先算**原始全局**间隔 `Δt_i = onset_seconds[i] - onset_seconds[i-1]`（秒），再乘以 100 变成厘秒（1 cs = 0.01 s）。首事件没有前驱，保留 `null`，不把乐曲开头静默算进首个间隔。

| 原始全局 onset 间隔 | KC 输出 | 典型解释 |
|---|---:|---|
| `0 ≤ Δt < 0.20 s` | `10` | 约 10 cs |
| `0.20 ≤ Δt < 0.40 s` | `30` | 约 30 cs |
| `0.40 ≤ Δt < 0.60 s` | `50` | 约 50 cs |
| `0.60 ≤ Δt < 0.80 s` | `70` | 约 70 cs |
| `Δt ≥ 0.80 s` | `90` | 约 90 cs，所有更长间隔统一封顶 |

这是将间隔四舍五入到集合 `{10,30,50,70,90}` 中最近的中心值，并将所有超过 90 cs 的间隔封顶为 90；边界恰好落在 20、40、60、80 cs 时**归入较大的一档**。例如 `0.25 s → 30`、`0.50 s → 50`、`0.75 s → 70`、`1.20 s → 90`。虽然档名是代表值 `90`，第五档实际覆盖的是所有 **≥80 cs** 的间隔，不能误当作仅 `≥90 cs` 才进入第五档。

**原始间隔不丢弃**：`intervals_seconds[i] = [left_interval, right_interval, global_interval]` 继续保留秒数，不做离散化。只有 `kc_vectors[i][3]` 是分箱后的值。该维只用 global，不混入左/右手间隔。

### 4.2 `change_count`：保持原始整数

`change_count_i = Σ_{h: C_{i,h} ≠ ∅} |C_{i,h} \ P_{i,h}|`。同音重复不算变化，第一次出现的手全部算新增；只包含目标乐谱信息，不利用学生实际表现。`change_count ≤ simultaneous_note_count`。如希望两者都不超过 10，应在数据清洗环节规定并校验不超过 10 个目标音；**任意原始 MIDI 本身并不保证这一上限**，不要在 KC 处直接截断计数。

### 4.3 `mean_move_bin`：先求原始移动，再划五档

计算未经离散化的平均移动距离 `d_i`：

1. 左手只与该手上一次目标起音匹配，右手同理；**不允许跨手匹配**。
2. 每只活跃手分别在上一手部目标音集合与本次目标音集合之间，先**最大化一对一匹配数**，再**最小化全部匹配的绝对钢琴白键位置距离**。令白键坐标为 `7*octave + KEY_X[pitch % 12]`，其中 `KEY_X=(0,.5,1,1.5,2,3,3.5,4,4.5,5,5.5,6)`；可用 Hungarian 最小成本匹配实现。
3. 令 `D` 为左右手匹配的白键位置距离总和，`M` 为匹配对数，`d_i = D / M`。包括距离为 0 的同音匹配；若 `M = 0`，`d_i = null`，最终 `mean_move_bin = null`。
4. `change_count` 是集合差集，不根据上述音符匹配推导。

将 `d_i`（单位：白键间距）转换成五个**类别 ID**：

| 原始 `d_i` | `mean_move_bin` | 描述 |
|---|---:|---|
| `d = 0` | `0` | 完全没移动（区别于无匹配） |
| `0 < d ≤ 2` | `1` | 很小的平均移动 |
| `2 < d ≤ 5` | `2` | 小至中等的平均移动 |
| `5 < d ≤ 9` | `3` | 较大的平均移动 |
| `d > 9` | `4` | 大幅度平均移动 |
| 没有任何匹配 | `null` | 信息缺失，不是第六种难度类别 |

这五档的阈值是**可调整的研究设计**，不宣称存在唯一的音乐学标准。专门保留 `d=0` 的单独档，是为了区分同音重复与真实手指位置移动。`mean_move_bin` 的输出是 **0–4 的类别索引**，不是半音距离代表值，也不能当作连续测量值解释。

**示例：1.0 秒事件**：左手 `[48]→[50,53]`，右手 `[62,65]→[62,65]`：

- `change_count = |{50,53}−{48}| + |{62,65}−{62,65}| = 2`。
- 最优匹配的白键位置总距离为 `1+0+0`、匹配对数为 `3`，因此 `d = 1/3 = 0.333333`，`mean_move_bin = 1`。
- `onset_interval = 1.0 − 0.5 = 0.5 s = 50 cs`，所以 `onset_interval_bin_cs = 50`。
- 最终 `kc_vector = [false, 4, true, 50, 2, 1]`。

**示例：1.75 秒事件**：左手 `[50,53]→[50,54]`：`change_count=1`；`d=(0+1)/2=0.5`，分箱为 `1`。右手当次没有 onset，保留右手上一次起音的音集合历史。

**首事件**：`change_count` 等于所有首次出现手的起音数，而 `onset_interval_bin_cs=null`、`mean_move_bin=null`，因为没有上一全局 onset，也没有可匹配的历史音符。

### 4.4 建模与信息保留

- `simultaneous_note_count`、`change_count` 保留原值；`onset_interval_bin_cs`、`mean_move_bin` 是离散特征。
- 输入神经网络时可将 `onset_interval_bin_cs` 的 `{10,30,50,70,90}` 映射为 embedding 索引 `0–4`，将 `mean_move_bin` 的 `{0,1,2,3,4}` 直接作为 embedding 索引。**`null` 用单独的 missing mask / padding 编码，不能和有效 0 混淆。**
- `onset_interval_bin_cs` 可以从原始 `intervals_seconds[i][2]` 或 `onset_seconds` 重建；`mean_move_bin` 可从目标 `pitches` 和手部历史重建。这里的离散化**只影响 KC 表示**，不会删除原始乐谱事件。
- 对分箱是否合理，可以对比固定阈值与仅在训练集上拟合的分位数阈值，在验证集上做消融，而不依据测试集调阈值。

## 5. Reject Reason：二级标签与反向偏移

### `reject_reason.event[i]`：整个 onset 二元正确性

| 值 | 含义 |
|---|---|
| `0` | 全部目标音符正确，且没有额外弹奏的音符 |
| `1` | 任何目标音符错误，**或** `extra_notes[i]` 非空 |

这是**派生值**，不可与逐音符标签和额外音符记录分别独立随意标注：

```python
reject_reason_event = int(
    any(code != 0 for hand in reject_reason_note for code in hand)
    or len(extra_notes) > 0
)
```

### `reject_reason.note[i]`：每个目标音符的错误类型

结构与 `pitches[i]` 严格一致：`[[左手每个目标音错误码...], [右手每个目标音错误码...]]`。

| 示例编码 | 名称 | 含义 |
|---|---|---|
| `0` | `correct` | 目标音正确匹配且满足误差阈值 |
| `1` | `missed` | 目标音没有对应的有效实际演奏音符 |
| `2` | `wrong_pitch` | 匹配到了一个实际演奏音符，但其音高与目标不一致 |
| `3` | `timing_error` | 匹配音符音高正确，但 onset 时间误差超出设定容差 |
| `4` | `wrong_pitch_and_timing_error` | 匹配音符的音高错误，且 onset 时间误差也超出设定容差 |

以上 **0–4 是本数据集唯一的错误码枚举**。多弹音不占用目标音错误码，始终写入 `extra_notes`；多个目标音错误同时发生时，event 标签由任一 note 非零或 extra note 推导。单个匹配目标音同时有音高和时间问题时，必须标为 `4`；`pitch_offset_semitones` 与 `timing_offset_ms` 仍保留各自的原始偏差。

### `timing_offset_ms[i]`

与目标音高结构一一对应。定义为 `actual_matched_onset_ms - target_onset_ms`：**正数为弹晚，负数为弹早**；漏音写 `null`。错音若能被 Matcher 匹配，也可以具有有效的 onset 偏差。本示例中 `180` ms 用于展示 timing error；示例假定容差为 150 ms，这不是强制的全局阈值。

### `pitch_offset_semitones[i]`

与目标音高结构一一对应。定义为 `actual_matched_pitch - target_pitch`（整数半音）；弹对为 `0`，高一个半音为 `+1`，低一个半音为 `-1`，漏音为 `null`。**对错音仍保留数值偏差**，不要把实际演奏音高替换掉 `pitches` 里的目标音高。

### `extra_notes[i]`

是当前 onset 对齐窗口中**无法匹配至任一目标音符**的实际演奏 MIDI pitch 列表，例如 `[72]`。**不存 `hand`，因为手部归属可能不可推断**。如果同一音高额外弹了两次且都需要被计数，可写 `[72,72]`。这个紧凑版本不保留额外音符的精确 onset 偏差；若以后要研究多弹的时间模式，需要在更底层的 Performance MIDI/原始事件里另行保留时间。

匹配时必须先决定哪些演奏音符匹配到目标音符，再将未匹配演奏音符写入 `extra_notes`；**不允许同一次弹奏既判为 `wrong_pitch` 的匹配音，又重复出现在 `extra_notes` 中**。额外音符必须被分配到唯一的 onset（可使用最近目标 onset + 固定容差与确定的边界处理规则），不可被相邻 events 重复计算。

## 6. 完整示例的 6 个事件

| 事件索引 | 目标时间 | 左手 / 右手目标 pitch | KC Vector（v1.1） | 错误情况 | event reject |
|---|---:|---|---|---|---:|
| 0 | 0.00 | `[48]` / `[60,64]` | `[false,3,true,null,3,null]` | 全对 | 0 |
| 1 | 0.50 | `[]` / `[62,65]` | `[false,2,false,50,2,1]` | 目标音 65 漏弹 | 1 |
| 2 | 1.00 | `[50,53]` / `[62,65]` | `[false,4,true,50,2,1]` | 目标全对但多弹音 72 | 1 |
| 3 | 1.75 | `[50,54]` / `[]` | `[true,2,false,70,1,1]` | 目标音 54 弹成 55 | 1 |
| 4 | 2.00 | `[]` / `[64,67,70]` | `[true,3,false,30,3,1]` | 目标音 70 晚 180 ms | 1 |
| 5 | 2.50 | `[55]` / `[64,67,70]` | `[true,4,true,50,1,1]` | 全对 | 0 |

因此该示例统计为：`notes_evaluated=18`、`notes_successful=15`、`chords_evaluated=6`、`chords_successful=2`。由于 6 个目标 onset 都有至少两个不同 `(hand, MIDI pitch)`，这 6 个事件均满足本示例的 chord 定义。事件 2 即使所有目标音符都正确，也因为多弹音而被标记为 event reject `1`。

本示例恰好所有**有匹配历史的** `mean_move` 都位于 `(0,2]`，所以 `mean_move_bin` 都是 `1`；这不表示五档定义无效，真实数据应验证不同类别的频率与分布。

## 7. 数据完整性与校验规则

在数据生成与训练前建议逐条校验：

1. `len(onset_seconds) == len(pitches) == len(intervals_seconds) == len(kc_vectors) == len(reject_reason.event) == len(reject_reason.note) == len(timing_offset_ms) == len(pitch_offset_semitones) == len(extra_notes)`。
2. `pitches[i]` 等逐音结构始终有两个手部数组，顺序为 `[left,right]`；某手没有当前 onset 时是空数组 `[]`。
3. `onset_seconds` 严格递增（同一秒的所有起音必须先合并）；首事件的 `kc_vectors[0][3]`、`kc_vectors[0][5]` 和 `intervals_seconds[0][2]` 均为 `null`。
4. `kc_vectors[i][3] == onset_bin(intervals_seconds[i][2])`、`kc_vectors[i][5] == move_bin(raw_mean_move_from_pitches)`；所有 KC 特征仅由**目标乐谱**计算，不使用学生表现；四项离散维度需核对范围与空值。
5. `reject_reason.event[i] == 0` 当且仅当该 event 每个 note reason 均为 0、且 `extra_notes[i]` 为空。
6. `notes_successful <= notes_evaluated`、`chords_successful <= chords_evaluated`；漏音对应的 timing / pitch offset 为 `null`。
7. 一个练习事件中的 `extra_notes` 不能与已经匹配的实际音符重复计数；多弹音不进入 `notes_evaluated` 的分母。
8. 未评估到的后续乐谱 onset 不可自动标为 `missed`；对部分中断尝试，应另按实际数据覆盖情况处理。

## 8. 如何用于 Knowledge Tracing

- **Event-level Binary KT**：`question_i = (exercise_id, event_index)`；`correct_i = 1 - reject_reason.event[i]`。
- **Note-level KT**：`question_{i,h,j} = (exercise_id, event_index, h, j)`；`correct = 1` 当且仅当 `reject_reason.note[i][h][j] == 0`。
- **Multiclass KT**：将每个音符的错误码作为多类别目标；`extra_notes` 可单独构造 event-level 的多弹目标。
- **KC-based KT**：用 `kc_vectors[i]` 表示当前题目的结构化特征，将 boolean 转成 0/1，对 `null` 值设置显式缺失掩码；对 `onset_interval_sec` 和 `mean_move` 等连续特征可根据训练集统计做标准化。

对于序列预测，**整次练习汇总的** `notes_successful`、`chords_successful` 等包含未来信息，不可作为同一次练习早期 onset 的预测输入。评估协议应明确采用按用户、按歌曲，或按时间的划分目标，以控制信息泄漏。

## 9. 实施建议与版本策略

- 在采集和传输中直接将 `events_data` 存为 JSON 对象；如需兼容旧 CSV 格式，可对其做 `json.dumps(events_data)`，再存为字符串，读取时 `json.loads(...)`。
- 每首乐谱的 `onset_seconds`、`pitches`、`intervals_seconds`、`kc_vectors` 与学生无关；长期可以将其抽到独立 **Score/Question Bank**，Attempt 仅存表现和键索引，减少冗余。
- 建议将评分阈值、错误码映射、匹配算法版本一起纳入 Dataset 元数据，不要在不变更版本的前提下静默更新。
- 本规范不记录音符持续时间；如后续研究连音、踏板和重叠发音，务必另存原始 MIDI，不要把这个 onset-only 表示当作唯一原始数据。

**文件校验**：配套 JSON 已验证可解析；6 条事件的左右手结构、逐音符数组长度、event 标签推导、KC 离散化规则和 18/15、6/2 汇总统计均已通过一致性检查。
