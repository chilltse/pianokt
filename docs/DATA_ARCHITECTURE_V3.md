# PianoKT Data Architecture V3

本文件是当前实现的架构入口。部署状态见实施报告；仓库代码不代表生产环境已切换。

## 数据流

浏览器创建 Visit / Attempt → API 分配不可变 Raw 上传对象 → finalize 在 PostgreSQL 封存活动并写 Outbox → Relay / PubSub 唤醒 Worker → 内存解析 MIDI / Telemetry → 原有 Reference Grouping / Alignment / KC → create-only Silver → 校验写回字节 → fenced PostgreSQL 事务登记 Run、更新当前指针和成绩、写完成事件 → 按需导出 Gold。

不再运行 Spark、Delta、JAR、定时物化任务或对象扫描。Bronze 是 Worker 内存中的基础校验和解析，不是另一套数据库。API / Worker / Relay / Exporter 仍使用普通 Python 镜像。

## 唯一权威来源

- 活动事实：student_visits、student_attempts、student_attempt_events。
- 上传状态和当前在线结果：piano_attempts；learner_key 是研究身份。
- 所有正式 V3 Silver：piano_alignment_runs。对象存在不等于已提交。
- 产品即时反馈 / Product Results 保持独立，不改变其数据契约。
- 曲库是独立领域：Song / Segment / Content SHA-256 与 Supabase piano-midi 资源保持不变。整曲不进入 Segment 推荐。

## Storage

`DATA_ROOT` 指向现有 Data Bucket，开发环境可用本地目录。不同环境使用不同部署及根路径，并且数据库行始终校验 source_environment。

```
raw/recordings/{learner_key}/{attempt_id}/reference.mid
raw/recordings/{learner_key}/{attempt_id}/performance.mid
raw/recordings/{learner_key}/{attempt_id}/performance-events.json
raw/events/
silver/alignment/{alignment_run_id}/matches.json
silver/alignment/{alignment_run_id}/result.json
gold/attempts/{dataset_version}/
gold/knowledge_tracing/{dataset_version}/
```

路径由 `storage_layout.py` 生成。Supabase 曲库的 `midi/{hash}.mid` 不属于上述路径，不移动。旧对象不回填、不扫描、不删除；只有显式 reprocess 可从已登记的旧 Raw 路径读取原始证据。

## 提交与版本

Migration 024 将既有 piano_attempts 标记 legacy，新建行默认 3。不将旧 READY 解释为 V3。Registry 存储输入身份、结果键与摘要、算法版本、配置摘要、质量、用途和提交时间。UPDATE 被数据库拒绝；同 ID / 同内容重试成功，不同内容拒绝。删除仅属于受控生命周期操作。

Worker 验证 lease token 与有效期，在同一事务内登记 Run、更新 READY、正式准确率和 Outbox。事务失败留下的 GCS 对象只是可恢复的孤立文件。质量不合格也可正式提交，但不进入默认训练导出。

Result JSON 保留 schema_version=3 的既有数学与结构语义，新增独立 `data_architecture_version="3"`。新增的架构标识不冒充 Alignment 算法版本。KC、对齐阈值、Reject Reason、Event 正确性映射均未更改。

在线 PyKT 仅读取 Registry 验证的当前 online Run。研究 Run 不自动进入在线历史。V3 学生状态使用 `pianokt:v3:{environment}:{learner_key}` 命名空间；旧远端状态不覆盖。相同命名空间内已有来源变化或重新排序仍拒绝增量同步。

参见 [数据契约](DATA_CONTRACT_V3.md)、[导出](DATASET_EXPORT_GUIDE.md)、[生命周期](DATA_LIFECYCLE.md)、[切换](DATA_ARCHITECTURE_V3_CUTOVER.md)。
