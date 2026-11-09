# PianoKT → PyKT 接口映射与兼容性验收

本次范围是 reference KC、HTTP 预测/隔离模拟和兼容性检查。没有替换线上 DemoRecommender，没有训练模型、加载权重、复制 KCEncoder、同步学生历史或修改生产数据库。调用方须先完成 PyKT 学生身份与历史的绑定；空历史概率不能代表已有学生的掌握情况。

## 实现入口

- `backend/pianokt_backend/pykt_integration.py`：显式字段映射、reference 特征缓存、全曲上下文切片、预测去重/还原、未知率统计、版本/revision 防护、非保留 rollout。
- `backend/pianokt_backend/pykt_verify.py`：独立 golden 验收；只有全部检查成功才输出兼容性 manifest。
- `backend/tests/test_pykt_integration.py`：离线及可选真实服务测试。
- `backend/tests/fixtures/pykt/`：实际部署 OpenAPI/model info 快照、完整 reference MIDI、训练 Onset 与服务编码/概率 golden。

## 显式位置映射

映射由 `FIELD_MAPPING` 固定，不能根据 `kc_N` 名称推断语义。当前验证的 labeler 为 `1.2.0`：

1. `has_black_key → kc_0`：JSON bool，选中双手音高包含黑键。
2. `simultaneous_note_count → kc_1`：JSON int，左右手分别去重后的音高数量之和；双手同音计两次。
3. `both_hands → kc_2`：JSON bool，该 Onset 左右手均有音符。
4. `onset_interval_bin_cs → kc_3`：JSON int/null；全局相邻 Onset 间隔按 `<0.2 / <0.4 / <0.6 / <0.8 / 其余` 秒映射到 `10/30/50/70/90`；首项为 null。
5. `change_count → kc_4`：JSON int，各手相对该手前次发音新增音高数之和；没有发音的手保留前次上下文。
6. `mean_move_bin → kc_5`：JSON int/null，各手最大匹配数下最小白键坐标移动距离，按匹配数加权平均；`0 / (0,2] / (2,5] / (5,9] / >9` 映射到 `0/1/2/3/4`；无可匹配前文时为 null。

直接复用现有 `extract_midi_reference` 和冻结的 `versions/v1_2_0.py`，包括现有分组容差及左右手推断规则。bool 不转 0/1，null 不补 0。歌曲候选不读取 performance MIDI 或 reject_reason。

`ReferenceCandidates.extract` 接收**完整** reference MIDI，先提取全曲，再按绝对秒 `[start, end)` 选择 segment。`onset_index` 是完整曲目中的序号，`onset_time` 保留完整曲目时间；重复 KC 和顺序均保留。如果仅有裁剪后的 MIDI，调用方必须提供完整原曲及范围，不能把片段文件冒充完整上下文。

## 当前部署与证据

2026-11-09 读取 `http://localhost:8000/openapi.json` 与 `/kt/model/info`，并阅读 PyKT 的 `api.py`、`schemas.py`、`encoder.py`、`engine.py` 和增量接口说明。实际服务为 CPU/FP32、seq_len=128，单次预测部署上限为 256（OpenAPI schema 的 4096 并非实际部署上限）。

- model_version：`sha256:06a498bf828cd2c1445c4dcda76e2edc724678825ab6dd3e9af7327de174737a`
- feature_version：`sha256:1b59db4d1a8156400138163b416b5008c24e9b048c03044e90937e3c4adc332b`
- 训练来源：用户确认的 `/Users/tse/my_code/long_mem_akt/pykt-long-mem-akt/data/pianokt/pianokt.json`。
- reference 来源：用户提供的 `/Users/tse/Downloads/sightread-main/public/music/songs/moonlight-sonata.mid`，完整 MIDI 已放入 fixture，并记录 SHA-256。
- 训练 attempt：`045b427e-ba3d-5f1c-af67-56269f8e7f76`。训练文件注明 synthetic、labeler 1.2.0，属于合成训练数据，不代表真实学生效果。
- 该 attempt 的 129 个 KC 与完整 reference 前 129 个 Onset 逐项、逐 JSON 类型一致；训练时间保留 6 位小数，验收允许 `1e-6` 秒误差，不放宽 KC 比较。
- 训练记录中的 MIDI 哈希属于 segment，和完整 reference 哈希不同；两者均留在 golden，不冒充相同文件。范围为 `[0,45.000018)` 秒。
- golden 的原始 KC 来自训练记录；attrs、pid、空历史概率来自上述真实运行服务，作为该部署的回归基线。129 项全部为已知题目，unknown question/field ratio 均为 0。

## 使用

先重跑 golden 验收，生成当前连接所需的 manifest（不应手写确认字段）：

```bash
PYTHONPATH=backend .venv/bin/python -m pianokt_backend.pykt_verify \
  backend/tests/fixtures/pykt/golden.json \
  --url http://localhost:8000 --output /tmp/pykt-compatibility.json
```

启用认证时设置 `PYKT_API_TOKEN`。验证 CLI 不读取环境代理，避免 localhost 请求被代理转发。HTTP 失败直接抛出异常，不回退到猜测编码或旧预测。

```python
import json
from pathlib import Path
import httpx
from pianokt_backend.pykt_integration import Compatibility, ReferenceCandidates, PyKTClient

contract = Compatibility(**json.loads(Path('/tmp/pykt-compatibility.json').read_text()))
rows = ReferenceCandidates().extract(
    Path('backend/tests/fixtures/pykt/moonlight-sonata.mid'),
    song_id='moonlight-sonata', song_version='catalog-revision-1',
    segment_id='part-1', start=0, end=45.000018,
)
with httpx.Client(base_url='http://localhost:8000', timeout=60, trust_env=False) as http:
    client = PyKTClient(http, contract)
    result = client.predict('pianokt-integration-example', rows)
    simulation = client.rollout('pianokt-integration-example', rows[:3], seed=7)
```

等价预测请求（只传原始向量，不传 attrs/pid）：

```http
POST /kt/students/pianokt-integration-example/predict
Content-Type: application/json

{"kc_feature_names":["kc_0","kc_1","kc_2","kc_3","kc_4","kc_5"],"candidates":[{"candidate_id":"0","kc_vector":[true,3,true,null,3,null]}]}
```

模拟使用 `POST /kt/rollouts/evaluate`，包含 `student_id`、同一字段列表、`sequence:[{"kc_vector":[...]}]`、`mode:"sample"`、`num_trajectories`、`seed`、`retain_branch:false`。forced 模式另外传每步整数 response；sample 模式禁止传 response。

## 版本、缓存与一致性

每份预测保留 state 中的 model_version、feature_version、student_id、history_revision，以及每个 Onset 的 kc_labeler_version、song_id、segment_id、song_version、reference_sha256、index/time。另记录 golden 的 evidence_sha256。

reference 缓存键为完整 MIDI 内容 SHA-256 + labeler 版本。预测缓存限定在一个 HTTP 客户端/认证作用域实例内，键包含学生完整 state（含 revision、模型/特征版本）、labeler、证据哈希以及原始 KC JSON。相同 KC 跨歌曲共享预测，但返回时重新绑定每首歌曲的身份和版本；不缓存旧歌曲关联。JSON 键不会混淆 false、0、0.0 和 null。

每次调用（包括缓存命中）重读部署信息及学生 state；预测按实际 max_candidates 拆批，每批响应与开始 state 必须一致，结束再检查。并发真实学习导致 revision 变化会报错，不拼接不同状态的概率，也不发布本轮缓存。缓存是实例内存缓存，没有自动跨进程共享；连接目标/认证身份不可在实例存活期间切换。

服务 KCEncoder 决定 attrs/pid：PAD=0 不应用于事件，UNK=1，MISSING=2，未知完整组合 pid=0。PianoKT 只验证/记录返回值，不创建词表。unknown_question_ratio 按还原后全部 Onset 计数；unknown_field_ratio 为未知字段数除以 `6 × Onset数`。

模型或 feature_version 不匹配立即拒绝，包括已有缓存。feature_version 当前是编码词表哈希，**不包含完整 labeler 语义**，因此仍需训练 provenance + golden + labeler 版本。发现语义差异时先确定训练时 extractor、手部推断及分段规则，恢复对应冻结版本并重新验收；如果必须改特征，则另行迁移数据/模型和学生历史。本任务不会自动迁移、重建或重新训练。

## 验证命令

```bash
# 离线单元/回归；真实服务项默认明确 skip
PYTHONPATH=backend .venv/bin/python -m pytest \
  backend/tests/test_pykt_integration.py \
  backend/tests/test_reference_features.py \
  backend/tests/test_direct_export.py -q

# 真实服务：固定模型、训练 golden、bool/int、null/0、UNK、pid=0、重复歌曲及状态不变
PYTHONPATH=backend \
PYKT_GOLDEN_FIXTURE=backend/tests/fixtures/pykt/golden.json \
.venv/bin/python -m pytest backend/tests/test_pykt_integration.py -q
```

真实验收仅使用独立 `pianokt-golden-*` 诊断学生空历史。只暴露 state/predict/non-retained rollout，不调用 observe、observe-batch、rebuild 或真实学生写操作。服务内部 recover 可能为诊断身份初始化缓存；这里保证的是事件历史/revision 不变，并非承诺服务完全没有内部存储动作。

## 已确认局限

golden 覆盖一首歌的 129 个训练 Onset；它不是全训练集一致性证明，也不是推荐效果评估。后续应扩充更多曲目、左右手切换、离散边界和不同片段起点的训练 golden。

full-reference segment 首项会保留前文；如果训练曾对某个裁剪 segment 重置上下文，该 segment 会与完整曲目切片存在差异，应单独验收并拒绝直接使用不一致训练语义。当前中途切片上下文由离线测试验证，真实训练 golden 选用起点为 0 的 segment。

没有完成全库遍历/数据库接入、实际学生历史导入、Sweet Spot、Target Gain 或推荐策略替换；这些超出本次“先完成映射和兼容性测试”的范围。HTTP 模拟返回原始统计结果，调用方自行选择业务策略；不会把它作为真实学习结果写回。
