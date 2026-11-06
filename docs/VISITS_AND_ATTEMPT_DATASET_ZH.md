# visit、attempt 与训练数据集导出

> 2026-11-04 更新：当前事件单表写入、来源隔离、封存字段及数据集 1.3 见 [v2 数据字典](DATA_CONTRACT_V2_ZH.md)。本文保留 Visit/Attempt 的研究定义；默认导出已为 Direct + production，旧来源 unknown 不自动纳入。

更新日期：2026-11-03。对应 activity、pipeline/attempts.py、selection.py、publication.py 和 migration 015。项目全景见 [架构总览](ARCHITECTURE_ZH.md)。

当前新增显式 `--source direct`：在同一 repeatable-read 快照内读取活动上下文与 PostgreSQL 当前 READY 指针，结束数据库事务后逐个读取 GCS、核对提交 SHA-256、身份与 schema，再复用原 exporter。它不依赖 Delta/Spark；省略参数仍走旧 `--source lake`。仅支持 migration 015 + 新 Worker 写入完整提交凭证的当前 run；历史无凭证结果明确报错，不能凭文件存在性回填。实际改动、部署和验收边界见 [简化实施记录](DATA_PIPELINE_SIMPLIFICATION_IMPLEMENTATION_ZH.md)。


## 1. 先区分三个层次

- visit：学生在一个标签页访问网站的窗口，站内切主页、排名、歌曲都不结束它。
- attempt：一次从实际播放开始到完成/退出/中断的演奏，暂停和继续属于同一次。
- alignment run：某次演奏的一版对齐结果；版本/输入/配置改变可产生不同 run。

当前完整演奏采集接在 Challenge。visit 是全站模块维护的；其他页面的浏览不自动产生一条完整 Challenge 训练样本。

## 2. 时间记录实际在哪

- student_visits：visit ID、用户、started_at、last_seen_at、ended_at、结束原因、是否推断及最新序号。
- student_attempts：attempt ID、visit ID、曲目、访问内序号、实际 started_at/ended_at 与 received_at。
- student_attempt_events：event ID/sequence/type、occurred_at、歌曲位置、有效播放累计、metadata。
- piano_attempts：录音上传与处理状态、对象摘要/指针、finalization 和 result 指针。
- user_play_logs：最终播放汇总，包括 time_playing、is_played_in_full、exit_status。

student_attempts 早于录音上传任务存在，因此未上传成功也可能留下演奏时间线。旧 play_events_raw/user_play_logs 的 session_id 表示播放周期，新 Challenge 将它与 attempt ID 对齐，不能填 visit ID。

## 3. 浏览器如何判断一次访问是否结束

页面刷新延续 visit；新标签页、重新打开或站外网页返回的 BFCache pageshow 建立新 visit。隐藏标签页仍属于当前访问，退出登录终止当前用户访问。

心跳每 30 秒一次。pagehide 尽力发送并标记推断结束，刷新恢复时可延续访问。若根本没有结束信号，student_visit_status 在最后在线超过 5 分钟后提供 effective_ended_at=last_seen_at 的估计，源表不会因此被定时写成精确关闭。

电脑断电、强杀浏览器或后台限频都影响观测。UTC 是统一比较时区的方式，不是校准设备时钟的方法；并列时间的稳定排序也不能证明现实中哪次真正先发生。

## 4. 活动、文件与最终提交

小型 activity 包先进入 localStorage 队列，再发 /activity。非法/冲突包保留在本地 rejected 区域；网络/认证问题可恢复重试。sessionStorage 只保存当前访问上下文。

完整演奏包结束后另存 IndexedDB，再走 create → 文件 PUT → finalize。未封存的正在录音片段不能靠 activity 数据重建出完整 MIDI。

服务端以同一 attempt 事务锁协调活动追加与 finalize；已最终提交的 attempt 不接受新活动 ID，相同已存事件可幂等重试。

## 5. attempt 主文件里的字段

主文件 `attempts.jsonl` 每行一个 attempt，使用 learner_key 作为伪名 user_id，包含：

- song_id、song_type、exercise_id、play_mode。
- visit_index、attempt_index、days_since_signup。
- 和弦/音符评估数、正确数、time_playing、is_played_in_full、exit_status。
- exercise_part_index、kc_labeler_version、events_data。

time_playing 来自最终汇总的有效演奏累计；is_played_in_full 采用字符串 t/f，finished 才为 t。数据库 abandoned 导出为 quit。没有预定义片段序号时 exercise_part_index 为 0。

exercise_id 由来源、歌曲与 assigned_range/手部/移调/waiting 等练习配置决定；score_version 记录本次裁剪参考的摘要。提前退出改变 score_version，不应因此把同一分配练习当成另一种 exercise。

events_data 是目标 onset 的列式结构：pitches、note labels、timing/pitch offsets 都用 `[左手,右手]` 形状，多弹音单独保留。完整字段见 [schema 说明](../pianokt_schema_README_v1_1.md)。

## 6. 顺序号和相对注册天数从哪来

编号由 exporter 读取上下文后计算，按学生分别从 1 开始：

- visit：UTC started_at → visit ID。
- attempt：UTC started_at → visit ID → visit_attempt_sequence → attempt ID。

attempt_index 跨 visit 排序。编号先覆盖 cutoff 前已经开始且已收到的上下文历史，再筛样本质量，所以最终文件可有缺号。

`days_since_signup = (实际 started_at - 注册时间) / 86400 秒`，保留六位小数。注册时间通过 backend-only piano_registration_time 查询；若开始早于注册则报错，不填零。

内部 attempt/visit ID、started_at/ended_at 和暂停/退出时间节点不重复放进主行；放在旁路元数据文件，通过 index_map 关联。

同一个 dataset_version 的编号固定。补传早期历史可能使后续新快照重编号；不能只用 user_id + attempt_index 跨不同快照永久关联。

## 7. 导出命令

先应用与代码匹配的 013、014，在安装后端包、加载 DATABASE_URL 的环境中执行：

```bash
RAW_ROOT=data/raw LAKE_ROOT=data/lakehouse \
  pianokt-data export-attempts \
  --cutoff='2026-11-03T00:00:00Z' \
  --output-prefix='datasets/attempts-20261103'
```

DATABASE_URL 不能放进浏览器。导出身份须读活动、learner、上传状态、最终汇总、注册时间函数及 raw/lake，并有 datasets 前缀创建权限。GCS 只需将两个 ROOT 换为授权的 gs:// 路径。

## 8. 哪些结果能进入导出

旧 source=lake 要求：raw result 对应 run 已在 ops/completed_runs，alignment_completed_at 早于 cutoff，显式质量通过，非 waiting。

attempt 导出另要求：有 student_attempt 和 visit；开始/接收时间早于 cutoff；learner 关联一致；有 cutoff 前的结束；最终练习汇总完整；事件数组形状有效。

没有旧访问上下文的历史结果被跳过，不凭上传时间编造 visit。结果已生成但未入湖完成、质量被拒绝、未结束或上下文不完整，都不能当作有效训练样本。

上下文在一个 repeatable-read 数据库事务中读取。cutoff 限制接受的历史与标签，元数据中的 visit 当前结束/在线状态仍是导出时捕获，不能据此声称重建任意历史时刻的全部页面状态。

## 9. 同一演奏有多个对齐版本怎么办

默认不自动选 latest。同一个 attempt 多个合格候选会报错。

显式提供 JSON 映射：

```json
{
  "attempt-uuid": "selected-alignment-run-id"
}
```

并在命令后加 `--run-selection selection.json`。指定后只导出映射集合；映射到不存在、未完成或超过 cutoff 的 run 会报错。选中的 run 仍需满足质量与上下文要求。

## 10. 成果文件与提交顺序

```text
RAW_ROOT/datasets/<prefix>/
├─ intent.json               固定发布计划与文件摘要
├─ attempts.jsonl            一次演奏一行，含两个顺序号
├─ visits.json               访问元数据
├─ attempt_metadata.json     演奏元数据与时间
├─ attempt_events.json       暂停/继续/退出等节点
├─ index_map.json            顺序号到内部 ID、run、score_version 的映射
└─ manifest.json             最后发布的版本、数量、过滤统计和摘要
```

旁路文件也覆盖上下文历史，不限于质量过滤后留下的训练行；index_map 则对应实际导出的记录。learner_key 是伪名，元数据与映射仍属于受控研究数据。

同一 prefix 只能代表同一发布计划。中断后同内容可重试；内容变化使用新 prefix。只有 manifest 成功发布并校验通过，才是一份可用数据集：

```python
from pianokt_backend.storage import Objects
from pianokt_backend.publication import read_bundle

manifest, files = read_bundle(Objects('data/raw'), 'datasets/attempts-20261103')
print(manifest['dataset_version'], manifest['count'])
```

## 11. 逐 onset 的另一个 exporter

`export-training --cutoff ... --output-key datasets/onsets.json` 输出单个 JSON，其中 examples 每条是一个 onset，带 learner_key、attempt/run、event_index、pitches、KC、correct/reject_reason、ordering_timestamp/ordering_source。

CLI 同样读取真实数据库上下文并使用 UTC/版本选择。兼容的无 context Python 调用可以回退旧上传时间，必须看 ordering_source，不能混作真实开始时间。

attempt 外层 manifest schema 是 1.2；events_data 与 kc_labeler_version 仍为 v1.1；完整 alignment JSON schema_version 为 3。根目录示例文件名 v1_1 保留，但编号为示意值，不是真实学生历史。

两个 exporter 只生成数据。学生/时间拆分、序列窗口、训练、评估和模型发布仍需要独立实现。

## KC 1.2.0 更新

当前新结果与导出使用 `kc_labeler_version: "1.2.0"`，取代导出中的 `feature_version`。events_data 保持列式形状，但 Reference onset 改为共享 Matcher 的 30 ms 首音锚定分组；历史无版本结果单独标识为 v1.1，禁止混合版本导出。缓存身份包含 KC 版本，升级需要重跑 Alignment，不修改旧结果。详细契约见 [KC Labeler 1.2.0](KC_ML/KC_LABELER_1_2_0.md)。
