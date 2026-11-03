# 当前验证记录与覆盖边界

更新日期：2026-11-03。本页汇总最近一次代码重构验收记录，并说明它能证明什么。当前项目架构见 [总览](ARCHITECTURE_ZH.md)，详细故障注入和环境见 [重构记录第 14 节](REFACTOR_AND_DATA_INTEGRITY_ZH.md#14-最终验收结果2026-11-03)。本次架构文档更新没有重新运行全部业务测试，也没有部署云端。

## 已实际执行的最近验收

- Python 3.13.12 + requirements.lock + 隔离 PGlite：44 passed，1 skipped。
- 被默认跳过的 Spark 测试单独在 Java 17 / Spark 4.0.1 / Delta 4.0.0 下执行：1 passed。
- Node 活动追踪测试：7 passed；上传队列测试：2 passed。
- 前端 check-types、build：通过。
- 本轮重构核心 TS/TSX 的 Prettier、infra/gcp/main.tf 的 terraform fmt、git diff --check：通过。

这里合计是 45 个后端测试分别执行通过，以及 9 个前端活动/上传测试；不是所有仓库命令都已通过的声明。

## 测试覆盖哪些实现

对齐测试使用当前 time-guided matcher，覆盖时间范围、质量门槛、左右手、标签与配置隔离，不再以旧 Parangonar 算法作为当前入口。

数据库测试覆盖活动归属、乱序/重复、RLS、最终提交回滚、旧事件原子 RPC、正式排行榜口径、worker lease fencing 和完成写入回滚。

存储/数据集测试覆盖 create-only、UTC 排序、上下文与质量筛选、版本选择、发布中断不可见、恢复及摘要损坏拒绝。

湖仓测试覆盖完成标记前失败不进入正式 reader、同 run 重放、版本保留。Spark 测试覆盖坏 JSON、checkpoint 恢复、提交集合和同 ID 异内容冲突。

前端 Node 测试覆盖 visit 的刷新/新标签页/BFCache/账号切换，以及上传队列网络失败保留、本地存储失败不发起远端写入。

## 仍不能由这些测试证明的事情

- 真实 GCS 签名、CORS、Cloud Run IAM、Pub/Sub 归档/重试/死信、真实 Supabase 角色和跨服务网络。
- 真实浏览器 IndexedDB 配额、设备断电与未封存录音恢复。
- 真人对齐标签准确率、正式能力模型、推荐学习收益或容量/延迟 SLA。
- 当前云账号已经运行了 migration 014 与匹配镜像。

全项目 check-fmt 仍有最近记录的 27 个既有文件格式问题，整个 infra fmt 还会涉及本机 terraform.tfvars。Bun 专用 parser/TimelineStrip/referenceSnapshot 测试最近没有在本机重跑，CI 保留对应步骤。

## CI 实际配置

`.github/workflows/validate.yml` 在 main 的 PR 上分三类执行：前端 Bun/Node 测试、格式/类型/构建；Python 3.13.12 + PGlite 后端测试；Java 17 + Spark 集成测试。CI 配置存在不等于远端最近一次运行已经全绿，需要查看实际执行结果。

`backend/tests/schema.mjs` 是早期 migration 009 的专项检查；包含 013/014 的活动与事务测试使用 activity_db.mjs，由 test_activity.py 调用。不要用旧 schema 检查覆盖全部迁移的结论。


## 2026-11-03 直接导出增量验证

本轮后端全套 53 passed、1 skipped，新增 DB＋GCS 来源对照和失败注入，事务测试加载 migration 015；Terraform validate 通过。部署/模型和性能门槛仍未验收。详见 [实施记录](DATA_PIPELINE_SIMPLIFICATION_IMPLEMENTATION_ZH.md)。

Spark 集成测试独立重跑 1 passed（74.63 秒）；direct 专项最终重跑 9 passed，READY/摘要/SQL cutoff 验证最终重跑 1 passed。新增导出路径不启动 Spark。
