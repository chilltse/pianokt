
# PianoKT 与 PyKT 模型的 KC 映射及接口集成

项目：PianoKT

目标：将 PianoKT 曲库中的歌曲及 song_segment 转换为与已训练 AKT-FMKC checkpoint 兼容的 Onset KC 输入，为后续 Batch Prediction、Sweet Spot Filtering、MC Rollout 和 Target Gain 计算提供可靠的数据基础。

## 1. PyKT 模型及接口

PyKT 仓库：
https://github.com/chilltse/pykt-long-mem-akt

分支：`feature/akt_fmkc_incremental_inference`

请先阅读：

- `pykt/serving/api.py`
- `pykt/serving/schemas.py`
- `pykt/serving/encoder.py`
- `pykt/serving/engine.py`
- `docs/akt_fmkc_incremental_usage.md`

PyKT 使用已经训练好的 checkpoint，并对外提供：

- `GET /kt/model/info`
- `POST /kt/students/{student_id}/predict`
- `POST /kt/rollouts/evaluate`

PianoKT 应通过 HTTP 调用服务，而不是自行加载模型权重或复制编码器。完整的模型的API文档你可以查看：   http://localhost:8000/docs#/

先核对当前运行服务的 OpenAPI 文档及 `/kt/model/info` 返回值，不要假设 GitHub 代码与实际部署版本完全一致。

## 2. KC 特征语义映射

PianoKT 原有的六维 KC 特征依次为：

1. has_black_key
2. simultaneous_note_count
3. both_hands
4. onset_interval_bin_cs
5. change_count
6. mean_move_bin

已检查的一份 PyKT checkpoint 配置中，字段名称为 `kc_0` 至 `kc_5`。

请确认这六个位置的实际语义、数据类型和离散化规则是否与训练时完全一致。

如果确认一致，建立显式的位置映射，不要根据字段名称猜测。

如果不一致，必须报告差异和迁移方案，禁止直接继续推理。

所有字段保留原始 JSON 类型，例如布尔值不能随意转换为 0/1，null 不能随意转换为 0。

## 3. 曲目到 Onset KC 的转换

对于每个 song / song_segment：

1. 获取 reference MIDI。
2. 使用与训练数据相同版本的 KC 特征提取规则。
3. 按时间顺序提取全部参考 Onset。
4. 每个 Onset 输出六维 kc_vector。
5. 记录 song_id、segment_id、onset_index、onset_time。
6. 保留原始 Onset 顺序和重复出现的 KC。

优先复用 PianoKT 当前的 feature extraction / KC labeler，不要单独重新实现一套近似规则。

如果 segment 是从完整歌曲截取的，优先在完整 reference 上计算上下文相关的 KC，再按 segment 范围切片，避免首个 Onset 因缺失前文产生不同特征。

不要使用学生 performance MIDI 或 reject_reason 来生成候选歌曲的 KC。

## 4. 词表编码与模型调用

PianoKT 只传递原始 kc_vector。

由 PyKT 的 KCEncoder 使用 checkpoint 内的训练词表生成 attrs、pid。

不得在 PianoKT 中自行分配 pid、重新构造词表或修改模型 embedding。

允许对重复 KC 进行去重预测，但映射回歌曲时必须保留原始出现次数。

记录每个候选的 unknown_question、unknown_fields，并统计未知比例。

## 5. 版本与缓存

每次推荐应绑定：

- model_version
- feature_version
- kc_labeler_version
- song_id / segment_id
- song_version
- student history_revision

歌曲 KC 特征可根据 reference MIDI 的内容哈希与 labeler 版本缓存；学生预测结果应根据学生状态 revision 和模型版本缓存。

版本不匹配时拒绝直接使用旧缓存。

## 6. 验收测试

新增独立映射测试，要求：

1. 构建一个 golden fixture：选择训练数据中已知的 Onset，保存其 reference MIDI、原始 KC 向量和模型编码结果。
2. 使用 PianoKT 当前 labeler 重新提取 KC，结果必须与训练时一致。
3. 使用真实 PyKT 服务验证 attrs、pid 和预测概率。
4. 确认字段顺序、bool/int 类型、null、UNK 和 pid=0 的行为。
5. 检查完整歌曲与 segment 切片的上下文一致性。
6. 检查同一 KC 出现在不同歌曲时预测结果相同。
7. 检查不同 checkpoint 或 feature_version 不会误用旧缓存。
8. 推荐预测和模拟过程中不改变学生真实状态。

## 范围限制

先完成模型接口映射和兼容性测试。

不训练新模型，不修改生产数据库，不改变当前学生数据，不重新实现 AKT。

完成后提供一份 `docs/PYKT_INTEGRATION.md`，包含字段映射、API 请求示例、版本兼容规则、测试方法和当前已确认的局限。