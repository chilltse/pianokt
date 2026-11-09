
# 取得最新的一组raw--三个文件
.venv/bin/python docs/tse/scripts/get_raw.py

# 示例--安装.venv依赖
.venv/bin/python -m pip install python-dotenv "psycopg[binary]"


# 测试本地新版模块化的kc alignment_debug
.venv/bin/python tests/local_debug/alignment_debug.py \
  --reference output/attempts/e232d375-e7dd-4d8e-ab56-11ff3b880efc/reference.mid \
  --performance output/attempts/e232d375-e7dd-4d8e-ab56-11ff3b880efc/performance.mid \
  --events output/attempts/e232d375-e7dd-4d8e-ab56-11ff3b880efc/performance-events.json \
  --observed-end 11.2377 \
  --reference-origin 0 \
  --kc-labeler-version 1.2.0 \
  --output output/tmp/pianokt-kc-check/my-resultgcp

# 输出身份与类型检查（更新后的本地结果）
# 为保留旧结果供对比，使用新输出前缀：
.venv/bin/python tests/local_debug/alignment_debug.py \
  --reference output/attempts/e232d375-e7dd-4d8e-ab56-11ff3b880efc/reference.mid \
  --performance output/attempts/e232d375-e7dd-4d8e-ab56-11ff3b880efc/performance.mid \
  --events output/attempts/e232d375-e7dd-4d8e-ab56-11ff3b880efc/performance-events.json \
  --observed-end 11.2377 \
  --reference-origin 0 \
  --kc-labeler-version 1.2.0 \
  --output output/tmp/pianokt-kc-check/my-resultgcp-verified

# events_data 新增 onset_ids、reference_chord_indices、ref_note_ids、reference_group_note_ids。
# ref_note_ids[event][hand][pitch_index] 是 ID 列表，保留同音高的多个源音符。
# onset_ids 仅在当前 Reference 快照/分组内有效，跨结果需连同 score_version / alignment_run_id。
# notes/raw_matches 的 pitch、索引等离散值输出为整数或 null，时间仍为浮点数。


## 自动读取数据库上下文

重新运行 `get_raw.py` 时，除三份录制文件，还会保存同目录的 `attempt-context.json`，包含真实 attempt_id、song_id、midi_keyboard_used、visit_id、observed_end_sec 和 reference_origin_sec。脚本从 piano_attempts 的 metadata.settings 读取范围/键盘信息，从已保存的 finalization.activity.visit.id 读取 visit_id；缺失标志输出 null，不伪造 false。

本地 alignment_debug 自动寻找 Reference 同目录的 attempt-context.json，因此下载后可以省略手动时间参数及 attempt-id：

```bash
.venv/bin/python tests/local_debug/alignment_debug.py \
  --reference output/attempts/e232d375-e7dd-4d8e-ab56-11ff3b880efc/reference.mid \
  --performance output/attempts/e232d375-e7dd-4d8e-ab56-11ff3b880efc/performance.mid \
  --events output/attempts/e232d375-e7dd-4d8e-ab56-11ff3b880efc/performance-events.json \
  --kc-labeler-version 1.2.0 \
  --output output/tmp/pianokt-kc-check/with-context
```

也可用 `--attempt-context /路径/attempt-context.json` 显式指定。命令行提供的时间值优先于 context；无 context 时保留原先 MIDI/telemetry fallback，不会自动查询 Supabase。

结果顶层增加 song_id、midi_keyboard_used、visit_id、reference_events_count。reference_events_count 是本次输出的已观测目标 Onset 数，即 len(events_data.onset_seconds)，由对齐后计算，不是数据库已有字段，也不是 MIDI 原始消息数量。Reference 音符总数/已观测数仍看 diagnostics.reference_notes_total / reference_notes_observed。

生产 Worker 使用相同上下文提取逻辑。修改需部署后才影响新结果；旧 GCS/result 和已经下载的输入不会自动更新。

## 仅提取候选曲库 MIDI 的参考特征

`with-context.json` 由上面的 `alignment_debug.py` 调用 `alignment.service.align`，再附加 attempt-context 生成；它包含参考特征、演奏响应、匹配结果与练习上下文。

只分析候选曲库时使用独立命令（纯本地，无需数据库、GCS、登录或演奏文件）：

```bash
.venv/bin/pianokt-data extract-reference ./candidate.mid \
  --kc-labeler-version 1.2.0 \
  --output output/tmp/candidate-features.json
```

目录批量提取（递归查找 .mid/.midi，保留子目录，每个文件输出一个 JSON，例如 candidate.mid.json；单个失败仍继续其他文件，最终退出码为 1）：

```bash
.venv/bin/pianokt-data extract-reference ./candidate-library \
  --kc-labeler-version 1.2.0 \
  --output output/tmp/candidate-features
```

输出是独立的 `artifact_type=reference_features`、schema_version=1，不是 alignment schema_version=3。它直接复用生产的 MIDI 解析、30ms Onset 分组、左右手推断与指定版本 feature_extraction，处理完整 MIDI，不读取旁边的 attempt-context，也不做演奏窗口裁剪。

- `midi_sha256`：输入文件字节的 SHA-256。
- `notes`：全部解析音符，含 ref_id、音高、力度、track/channel、onset_tick、起始秒和持续秒。
- `hand_mapping`：推断的左右手 track；`excluded_note_tracks` 列出未参与左右手 KC 的有音符轨道，请审查多轨候选曲。
- `events_data`：沿用对齐输出中的参考字段：onset_seconds、onset_ids、reference_chord_indices、reference_group_note_ids、ref_note_ids、pitches、intervals_seconds、kc_feature_names、kc_vectors。
- `pitches` 每个事件按 [左手音高列表, 右手音高列表] 排列；`intervals_seconds` 是 [左手间隔, 右手间隔, 全局间隔]，首次无前序事件时为 null。
- 不生成 attempt_id、演奏匹配、错误标签、偏差或评分。Onset/ref IDs 只在当前 MIDI 和分组规则内有效，请连同 MIDI SHA 与提取器版本保存。

左右手沿用当前生产推断规则：单个有音符轨道通常全部视为右手（明确的 left/LH/bass 名称除外），不会自动把单轨钢琴拆成双手；多轨也不会默认将所有伴奏轨纳入 KC。输出目录重复运行会覆盖同名 JSON。

当前六维 KC 的顺序由 `events_data.kc_feature_names` 给出，分别为黑键标志、同时音数、双手标志、Onset 间隔分箱、换音数、平均移动距离分箱。分箱值不是原始距离或原始时间；原始音符时间及事件间隔可从 notes/intervals_seconds 读取。
