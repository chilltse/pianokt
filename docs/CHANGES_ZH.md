# PianoKT 工程演进与当前文档索引

更新日期：2026-11-03。此文件概括演进关系；当前架构以 [架构总览](ARCHITECTURE_ZH.md) 和 [实现参考](PIANOKT_SYSTEM_REFERENCE_ZH.md) 为准，具体事务重构见 [变更记录](REFACTOR_AND_DATA_INTEGRITY_ZH.md)。

## 1. 保留的前端基础

原浏览器练习应用继续提供曲目加载、播放器、谱面/落键显示、音色、MIDI/键盘输入、普通练习/Freeplay/训练等能力。新增数据链路集中接入 Challenge，没有把所有原有页面改成服务端驱动。

## 2. 保存与异步处理

新的 Challenge 从浏览器直传私有 GCS；FastAPI 验证 Supabase 身份、上传证据和最终提交。PostgreSQL 保存上传状态、录音列表、汇总及 outbox；relay 将消息发到 Pub/Sub，私有 worker 处理上传完成通知。

浏览器从仅缓存上传 ID，演进为 IndexedDB 封存完整 owner-scoped 录音包；服务器从独立写入，演进为最终提交事务、身份冲突检查、幂等重试与 worker 租约校验。

历史 Supabase Storage 录音保留下载兼容，新录音不再走旧存储上传路径。

## 3. 对齐与训练结构

当前使用 time-guided-chord-monotonic-v3。历史 Parangonar 和双手聚合原型不是现行生产入口。对齐、手部判定、音符错误码与 onset/KC 构造拆为独立模块。

当前完整 result schema 为 3；列式 events_data 为 v1.1；attempt 数据集外层为 v1.2。visit/attempt 的真实时间线另表保存，导出使用 visit_index/attempt_index。

## 4. 分析层与原子发布

对齐结果由 delta-rs 写入 Bronze/Silver/Gold；事件归档由 Spark AvailableNow 整理。正式 reader 使用完成集合，版本按 alignment_run_id 保留。attempt 数据集使用 create-only intent/files/manifest 发布，并校验摘要。

## 5. 推荐与测试

推荐入口和曝光/点击记录已经接通，模型仍为显式 DemoRecommender。当前导出不包含自动模型训练。

最近测试与未验证云边界见 [验证记录](VALIDATION_ZH.md)，不能把本地测试通过当成云端部署完成。

## 6. 2026-11-03 文档统一

本次从实际源码重新整理：新增简单架构总览；重写系统参考、逐次演奏教程、登录录音、本地开发、部署与数据集说明；更新 README、验证记录和本文件。

删除旧正文中把 Parangonar、仅 ID 缓存、旧录音 RPC、逐 attempt 覆盖、每日一次调度和页面对齐查看器当作当前行为的叙述。历史重构记录继续保留其日期、测试结果和实施边界。
