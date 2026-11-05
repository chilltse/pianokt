# Challenge v2 保存与隔离验收

更新：2026-11-04。本版本**需要 migration 016** 和前后端协调升级。旧 v1 队列不迁移，不会自动恢复。代码修改未部署云端。

## 自动化检查

```bash
npm run test:uploads
npm run test:activity
node scripts/test_upload_protocol.mjs
node scripts/test_player_pause.mjs
npm run check-types
npm run build
PGLITE_MODULE=/private/tmp/pianokt-test-db/node_modules/@electric-sql/pglite/dist/index.js PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
```

PGLITE_MODULE 指向独立安装的测试库。真实 PostgreSQL 并发测试另用 `PIANOKT_TEST_POSTGRES_DSN`，CI 配置专用 postgres:17 service；它会创建/删除随机测试数据库，**不得指向生产服务器**，从不读取 DATABASE_URL。未配置时这三项测试明确 skip；Spark 测试需专用 Java/Spark 环境。

覆盖：v2 create/finalize HTTP流程、文件digest错误后重试、无实时事件时补齐、实时包乱序和重复、封存后幂等/拒绝变更、interrupted不可覆盖、事务回滚、来源不可重绑定、环境过滤在索引之前、投影重建与评分语义、长小数和一ULP真实冲突。前端覆盖上传协议、local lab禁用Activity、暂停时钟、持久化与owner恢复。

## 部署后验收

本次本地验证结果（2026-11-04）：后端 **70 passed / 4 skipped**；跳过项为 3 项真实 PostgreSQL 并发测试和 1 项 Spark 测试。前端 Activity、上传恢复、v2 协议、暂停时钟测试，以及类型检查、生产构建、`terraform validate`、`git diff --check` 均通过。真实 PostgreSQL 并发测试已接入 CI 专用数据库，但本次未在本机或远端 CI 验证其运行结果。

全仓 `npm run check-fmt` 未通过：26 个本次未修改的 TypeScript 文件存在既有格式问题；本次修改的 TypeScript 文件没有格式告警。现有 CI 仍执行全仓格式检查，因此该项仍会阻止 CI 全绿，需要另行整理这些文件。以上不代表云端迁移、浏览器端到端验收或实际 IAM/CORS 已通过。

1. 正常完成、主动退出、多次暂停/继续各录一段。检查 `/attempts` 只有文件元信息和 schema_version=2；finalize 为完整 Activity；所有请求应成功。
2. 断网演奏后主动退出：IndexedDB `pianokt-upload-recovery-v2 / bundles` 保存完整包；恢复同一账号后补传。只有 finalize 成功才删除。切账号不发送其他owner的包。
3. 刷新/关闭页面后打开：已封存v2包可恢复。尚未封存的演奏仅能记录 interrupted，不承诺恢复内存MIDI。
4. 检查 student_attempt_events 的完整sequence和 student_attempts.sealed_at/sealed_event_count；play_events_raw 不新增；outbox只出现一次performance.uploaded。
5. 延迟 `/activity` 直到finalize后，相同事件成功；更改事件内容或追加interrupted应409。已有interrupted的冲突终止包必须拒绝，不能覆盖证据。
6. PUT 412后仍必须通过服务端摘要校验；同Key不同内容应拒绝。后台重试日志不能仅看PUT状态。
7. Worker完成后核验 READY + result_digest/result_committed_at；质量通过分数才aligned，拒绝结果为rejected。client_success不被Worker分数改写。
8. 在开发API录音：四张有source_environment的表应为development。production导出不含这些Visit/Attempt/Event/Result，不占正式序号；公开榜单不含开发录音。
9. 执行rebuild-projections后两张投影与原始封存输入/summary一致，不新增outbox。数据库操作应在测试环境验收。
10. 使用新输出前缀导出，校验manifest所有文件摘要。相同计划重放成功，改变内容复用旧前缀应失败。

## 已知边界

升级后旧v1包不会重放；浏览器关闭后不会继续执行JS；只有IndexedDB已提交的数据具备恢复基础。后台永久冲突会保留证据，目前没有全局同步状态面板。

DB和GCS没有共同物理事务：文件可能先于finalize存在；只能按UPLOADED/READY及Manifest读已提交事实。发布验证通过不代表生产环境已应用相同schema/IAM/CORS/调度。
