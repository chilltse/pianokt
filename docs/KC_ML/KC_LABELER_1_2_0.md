# KC Labeler 1.2.0 实现契约

主入口是 `alignment.service.align()`；生产 Worker 和本地 `/align` 都使用此入口。

Reference MIDI → observed range 裁剪（含结束边界 30 ms 容差）→ 一次 `group_chords()` → 同一份 Chords 分别送往 Matcher 和 `feature_extraction.extract_reference_features()` → `events.build_attempt_events_data()` → result → Attempt / Training Dataset。

默认分组容差为 0.03 秒，相对每组首音判断，不采用相邻间隔传递；0、20、40 ms 分为两组，规范 onset 是组成员时间中位数。1.2.0 因此与旧版精确时间分组存在事件数及派生 KC 差异。六维名称、顺序、分箱、手部历史及 events_data 列式形状保持原契约。

`feature_extraction/` 只计算 Reference 特征，保存全部源 ref_id 及手部目标 pitch 的 ref_id 列表。未被左右手选中的音轨仍参与全量身份验证。`events.py` 验证每个 match/deletion 的 ID、组索引、pitch、track、tick 和 snapshot-relative onset；禁止缺失、重复、跨组和重复消费 Performance ID。插入音只分配一次到最近规范 onset。同手同 pitch 合并为一个目标，但只有所有原始响应的错误码与 offsets 完全相同时才接受；冲突抛出 `ReferenceGroupingMismatch`。

该异常在 Worker 中直接进入 FAILED，保留内部 error_code，不归类 INVALID 上传错误、不重试、不发布成功 result。旧的 `(df, reference_path)` events API 明确拒绝，不按时间猜测身份。

KC 唯一版本字段为 `kc_labeler_version`。版本实现及其标识集中在 feature_extraction/versions/v1_2_0.py，默认版本由 registry.py 管理；新 result 顶层、Attempt Record、Manifest、index_map、training 顶层及 runs 都传播实际版本。新导出不再写 feature_version。没有版本字段的历史 result 明确标识为 `v1.1`；可单独导出，混合不同 KC 版本则在写文件前报错。空导出使用当前版本。

run ID 身份加入 KC 版本，Worker 缓存同时检查该字段。版本变化会得到新的 create-only result 路径，需重跑 Alignment；同版本重试保持幂等。没有覆写旧 Artifact，也没有实现免 Alignment 的离线重标注。ReferenceFeatures 和已有 Alignment DataFrame 可独立传入 response builder，供未来离线工具复用。

当前不涉及生产部署、GCP 配置、数据库 schema 或历史数据回填。既有示例及 output 下历史 dataset 保持原样。


## 版本目录与扩展

- `feature_extraction/__init__.py`：统一提取入口、版本解析、可用版本查询，以及实现返回值的版本/维度校验。
- `registry.py`：显式版本注册表及默认版本；未知版本抛错，不回退默认。
- `types.py`：共享 ReferenceFeatures 和 ReferenceGroupingMismatch；特征名称和版本随结果传递。
- `versions/v1_2_0.py`：独立的 1.2.0 算法实现。

目前只注册 1.2.0。未来新增版本应增加独立实现文件及注册项，保留旧实现；不要创建未实现的占位版本。service、events、Worker 和 exporter 不导入 versions 子模块。提取入口及 `align()` / `alignment_run_id()` 均支持关键字参数 `kc_labeler_version`；未指定时由注册表选择默认版本。显式选择版本也参与 run ID，防止缓存串用。

中间结构携带 `kc_feature_names`，事件输出和 Attempt 形状校验按该版本维度数验证。新增特征维度仍需评估具体下游模型的输入兼容性，不能直接把不同版本混到同一 Dataset。

## 本地验证

在仓库根目录，使用现有虚拟环境，无需数据库或 GCP：

```bash
.venv/bin/python -m pytest -q backend/tests/test_reference_features.py backend/tests/test_time_guided_alignment.py backend/tests/test_attempt_dataset.py backend/tests/test_direct_export.py
.venv/bin/python -m pytest -q backend/tests
```

第一条覆盖分组、ID 篡改、重复音符冲突、响应错误码、版本注册、未知版本拒绝、不同维度、默认/显式版本隔离、缓存及导出。第二条运行完整后端；未配置 PGLITE_MODULE、专用 PostgreSQL 或 Spark 的集成用例会按原测试门控跳过。不要将测试连接配置指向生产数据库。

用真实 MIDI 和录制 sidecar 检查完整本地流程：

```bash
.venv/bin/python tests/local_debug/alignment_debug.py \
  --reference /绝对路径/reference.mid \
  --performance /绝对路径/performance.mid \
  --events /绝对路径/performance-events.json \
  --observed-end 7.9 \
  --reference-origin 0 \
  --kc-labeler-version 1.2.0 \
  --output /tmp/pianokt-kc-check/result
```

用实际录制值替换 observed-end（Reference snapshot-relative 秒）和 reference-origin（原曲绝对起点秒）。没有 sidecar 可省略 `--events`，但会进入旧 MIDI-only 校准路径，可能因证据不足被判质量无效。命令只读输入，写本地 JSON 和 SVG，不上传 GCS。

检查 `/tmp/pianokt-kc-check/result.json`：顶层版本为 1.2.0；events_data 的 onset、pitches、六维 KC、reject reasons 与 offsets 对应；summary 的质量标志及 diagnostics 给出拒绝原因。打开同目录 SVG 可查看 Reference/Performance 匹配。未知版本（例如当前未实现的 1.3.0）应失败，不产生新成功结果。

## 输出身份和整数类型

新 events_data 增加以下列式数组，长度均与 onset_seconds 一致：

- `onset_ids[i]`：`o{reference_chord_index}`，仅在该 Reference 快照及分组配置内有效。跨快照/结果关联时必须连同 score_version / alignment_run_id 使用。
- `reference_chord_indices[i]`：Matcher 原始 Reference 组索引；未参与手部 KC 的组可能使索引不连续。
- `ref_note_ids[i][hand][pitch_index]`：非空字符串列表，对应同位置 pitches；同手同音高的重复音符保留全部 ref_id，因此比 pitches 多一层列表。
- `reference_group_note_ids[i]`：该组全部 Reference ref_id，包括未参与左右手 KC 的音轨。仅含未选音轨的组不会生成 events_data 行，但仍在完整 Reference/Alignment 一致性校验范围内。

完整 Attempt 导出保留这些字段；精简 training 样本仍按原事件特征契约输出。历史 events_data 可以缺少这组身份列，读取器不得根据浮点时间补猜 ID；一旦提供身份列，则必须完整、同形、无重复消费。

notes / raw_matches 在 JSON 边界将 pitch、track、tick、channel、velocity、chord index、reject_reason 规范为整数或 null；秒数及偏差仍为浮点数。分数型整数域值会抛出内部契约异常，不截断。JSON Schema 的 integer 按数值语义可能接受 50.0，但严格语言类型/序列化工具可能拒绝，故输出直接使用 50。

本次输出契约标识纳入 alignment_run_id 身份输入，避免复用旧的缺少事件身份字段/整数规范化结果；不增加另一套 KC 版本字段，算法版本仍为 1.2.0，旧不可变 GCS 对象保持原样。
