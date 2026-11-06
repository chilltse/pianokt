
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
