# [PianoKT](https://www.pianokt.com/)

PianoKT 是一个浏览器钢琴练习应用。前端负责 MIDI 输入、乐谱播放、可视化和即时反馈；Challenge 录音经私有 GCS 保存，由 Python 后端异步对齐评分，支持按需直接从 PostgreSQL＋GCS 导出有序训练数据；旧 Delta 路径保留作并行验证基线。推荐入口目前使用明确标识的演示规则。

## 先读哪份文档

- **第一次了解项目**：[当前架构总览](docs/ARCHITECTURE_ZH.md)。用简单语言说明系统分工、数据流与 visit/attempt/run。
- **跟着一次演奏读代码**：[代码教程](docs/PIANOKT_TEACHING_GUIDE_ZH.md)。
- **查询具体实现**：[模块、数据库、接口和文件参考](docs/PIANOKT_SYSTEM_REFERENCE_ZH.md)。
- **实际运行**：[本地开发](docs/LOCAL_DEVELOPMENT_ZH.md)、[运行与部署](docs/DEPLOYMENT_AND_ROADMAP_ZH.md)。
- **查身份与数据规则**：[登录与录音](docs/AUTH_AND_RECORDINGS.md)、[访问与数据集导出](docs/VISITS_AND_ATTEMPT_DATASET_ZH.md)。
- **查改动和验证边界**：[重构与数据完整性记录](docs/REFACTOR_AND_DATA_INTEGRITY_ZH.md)、[验证记录](docs/VALIDATION_ZH.md)。

## 当前实现范围

完整的“活动记录 → 录音封存上传 → 对齐 → 入湖 → 数据集”采集接在 Challenge。普通练习、Freeplay 和训练页面具有各自前端功能，不能一概视为完整训练样本来源。

数据库内用事务保证关联写入；跨存储通过不可变文件、幂等重试和提交标记实现逻辑原子性。当前没有自动训练、正式能力模型或跨存储永久删除用户数据的完整流程。

文档按 2026-11-03 的当前源码及 migration 015 整理。仓库实现、测试通过与真实云端部署是不同状态，生产环境需另行核对。

数据链路简化的实现、启用步骤和待验收事项见 [变更记录](docs/DATA_PIPELINE_SIMPLIFICATION_IMPLEMENTATION_ZH.md)。

### Challenge 数据架构 v2

当前上传协议为 v2，需 migration 016。参阅 [架构与数据流](docs/ARCHITECTURE_ZH.md)、[Supabase/GCS 完整字段字典](docs/DATA_CONTRACT_V2_ZH.md)、[升级步骤和注意事项](docs/DEPLOYMENT_AND_ROADMAP_ZH.md)。默认研究导出为 Direct + production；旧浏览器队列不自动转换。

- [异步 finalize v3：状态、恢复和 migration 017 部署](docs/ASYNC_FINALIZATION_ZH.md)
