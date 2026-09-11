# 交付验证记录

本记录针对自动清理后恢复并重新构建的最终工程。先前临时版本的测试结果不作为这份交付的唯一依据。

| 检查 | 结果 | 范围 |
|---|---|---|
| Python 核心/接口/双手测试 | 11 passed；Spark 项默认跳过 | 真实 Parangonar、时间锚点、配置隔离、不可变文件、Delta 重放与质量失败、训练 cutoff/mask、基本鉴权与原白名单策略、双手轨号映射、左手单轨 |
| Spark 集成测试 | 单独启用后 1 passed | 真正启动 Spark 4.0.1 与 Delta 4.0.0，坏 JSON、Bronze/Silver/quarantine、同 checkpoint 重启 |
| PostgreSQL schema 测试 | PASS | PGlite 执行原迁移 006 与新增 009，outbox 随事务回滚、伪名字段、用户 RLS 隔离 |
| TypeScript | `npm run check-types` 通过 | React Router 类型生成与 tsc |
| 前端生产构建 | `npm run build` 通过 | 前端应用静态构建；保留已有大 chunk 警告 |
| Terraform | HCL 语法解析通过，27 个 resource block | 当前执行环境取消了 Terraform 运行所需审批，未完成 provider validate/plan/apply |

以下不是已通过的验收项：真实 Supabase 与 GCS 签名上传联调、Cloud Run IAM/OIDC、GCP Delta 读写、Docker 镜像构建、真实浏览器演奏测试、Cloud Run 容量压测、真实学生对齐准确率、真实推荐模型效果。

Python 测试会出现依赖弃用提示，以及单轨默认右手的显式警告。没有把这些提示静默屏蔽，也没有因为合成 MIDI 得到 100% 正确就声称真实用户预测准确率为 100%。

新增的双手回归测试确实发现并推动修复了一个原型问题：Partitura matcher 轨号与原始 mido 轨号不一致。最终测试覆盖含 tempo-only 轨的双手 MIDI，确认左右手均有正确标签。

运行命令、云端验收清单和失败恢复方法见 `DEPLOYMENT_AND_ROADMAP_ZH.md`。CI 包含后端测试与 Terraform init/validate；CI 定义已经提供，但没有在你的 GitHub 仓库实际触发。
