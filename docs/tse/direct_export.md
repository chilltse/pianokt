在(.venv)下

python -m pip install --no-deps -e ./backend



set -a
source backend/.env.local
set +a

pianokt-data export-attempts \
  --source direct \
  --environment production \
  --cutoff 2026-11-05T00:00:00Z \
  --output-prefix datasets/attempts-时间


  pianokt-data export-training \
  --source direct \
  --environment production \
  --cutoff 2026-11-09T23:00:00Z \
  --output-key datasets/training-20261109-test2.json


gcloud storage cp \
  "${RAW_ROOT%/}/datasets/training-20261109-test2.json" \
  output/training-20261109-test2.json
  


  ## 转化成按学生划分的训练数据


## 练习上下文字段

`export-training` 的 `examples` 是逐 Onset 的训练样本，并不是完整 alignment result 的复制。每次练习的上下文保存在 `runs[]`，通过 attempt_id/alignment_run_id 关联：

- `midi_keyboard_used`：原始练习的键盘标志，未知为 null，保留 false 与未知的区别。
- `visit_id`：对应的 visit UUID。
- `reference_events_count`：该结果 events_data.onset_seconds 的长度，不是原始 MIDI 消息数量。

Direct 导出已验证 GCS 结果后，对旧结果缺少的键盘标志使用同一次数据库快照中的 piano_attempts.metadata.settings；visit_id 使用 student_attempts 上下文。无需重跑对齐或修改旧 GCS result。字段出现冲突会中止导出。Lake 导出只能使用其结果内已有的键盘设置，缺失时为 null。

更新本地代码后重新执行 export-training、下载导出文件，再运行 `scripts/tse/group_pianokt_by_student.py`。整理后的 `students[].visits[].attempts[]` 同样包含上述三个字段。已经生成的导出文件不会自动更新；建议新建 output-key 保留旧导出。

每条 `examples[]`（整理后为 `students[].visits[].attempts[].events[]`）保留详细事件数据：

- `onset_seconds`：当前 Onset 的起始秒，单个数值。
- `pitches`：按 [左手音高列表, 右手音高列表] 排列。
- `intervals_seconds`：[左手间隔, 右手间隔, 全局间隔]，无前序事件时为 null。
- `reject_reason`：对象，含 `event`（当前事件的 0/1 标签）与 `note`（按左右手、音高顺序排列的逐音符错误码）。此前的整数形式改为 `reject_reason.event`；`correct = 1 - reject_reason.event`。
- `timing_offset_ms`、`pitch_offset_semitones`：按 [左手列表, 右手列表] 排列，与 pitches 逐项对应，保留 null。
- `extra_notes`：关联到当前 Onset 的额外演奏音高列表，保留空数组及重复音高。

上述值直接取自已提交 alignment result 的 events_data，同一事件索引切片，不重新计算或压缩逐音符结果。完整事件数组及左右手形状校验通过后才发布导出。


## 按学生、Visit、Attempt 整理

```bash
python scripts/tse/group_pianokt_by_student.py \
  output/training-20261109-test3.json \
  output/organized/training-20261109-test3.json
```

输出为 `students[].visits[].attempts[].events_data`，grouping_schema_version=2。
每个 attempt 的公共信息只保留一次；events_data 使用与 alignment result 一致的列数组布局，包括 pitches、onset_seconds、intervals_seconds、kc_vectors、reject_reason.event/note、timing_offset_ms、pitch_offset_semitones、extra_notes，并保留训练 correct 数组。原 runs 元数据并入对应 attempt。

学生按 learner_key 排列；Visit 按真实 visit_started_at 排列，Attempt 按 ordering_timestamp 排列，统一比较 UTC 时间；时间相同用 ID 稳定排序。旧导出没有 visit_started_at 时，Visit.started_at 为 null，按最早已导出 Attempt 排序，并标记 ordering_source=earliest_exported_attempt。这只代表导出范围内的顺序。新 export-training 同时导出 visit_started_at 和 kc_feature_names；旧文件缺少特征名称时不猜测填充。

事件按连续 event_index 重建数组。重复事件索引、身份冲突、缺失 visit_id 或时间冲突会报错，不合并不确定的记录。此脚本只重新组织训练导出已有字段；原始匹配、diagnostics、summary 等未导出的字段不会生成。
