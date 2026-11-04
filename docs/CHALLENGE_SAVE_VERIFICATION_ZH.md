# Challenge 保存修复验证

本次不需要数据库迁移。后端与前端都需要重新部署。未修改线上数据；线上历史失败请求的具体差异仍需结合日志确认。

## 自动化检查

在项目根目录执行：

```sh
npm run test:uploads
npm run test:activity
npm run check-types
# PGLITE_MODULE 指向已安装的 @electric-sql/pglite/dist/index.js 的绝对路径。
PGLITE_MODULE=/private/tmp/pianokt-test-db/node_modules/@electric-sql/pglite/dist/index.js PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_activity.py backend/tests/test_online.py -q
```

后端覆盖 extra_float_digits=0/1 下长小数事件首次写入、重复提交、cycle 提交/重试，以及相差一个浮点 ULP 的真实冲突。连接测试验证显式请求 extra_float_digits=1；这不是 Cloud Run 实际连接配置的证明。

前端覆盖落盘失败不发送请求、本地落盘不等待网络、并发恢复只发送一次、远程失败保留数据、按用户恢复。

## 部署后手工验收

1. 打开 DevTools，Network 勾选 Preserve log。弹奏、暂停、继续、Exit，再单独测一次自然结束后 Back to Home。应能离开；`/activity`、`/attempts` 和 `/finalize` 应成功，不能只以页面跳转判断云端保存成功。
2. 将网络设置为 Slow 3G，重复 Exit。本地落盘后即返回首页，不等待文件上传或 finalize；自然结束弹窗的按钮只在本地落盘阶段短暂禁用。
3. 已加载曲目后切换 Offline，再弹奏并 Exit。检查 Application → IndexedDB → pianokt-upload-recovery → bundles，完整录音包应仍在。退出应成功。联网并保持同一用户登录后，观察自动补传；仅在 finalize 成功后对应 bundle 才删除。
4. 离线保存后刷新页面，再联网，确认同一用户能恢复提交。切换到另一个账号时不得上传原账号的 bundle。不要清除站点存储。
5. 快速连续点击 Exit，确认同一 attempt 不会生成两个终止事件或两条录音。测试自然结束后立即返回首页的路径。
6. 对先前留在 IndexedDB 的失败录音，更新版本后以原用户登录并等待恢复。若仍返回 409，保留响应 detail、attempt ID；查 Cloud Run 日志 `Activity identity conflict attempt=... event=... fields=...`，定位具体事件和字段。不要通过换 event ID、清库或忽略 409 绕过校验。

## Cloud Run 连接确认

在使用与服务相同镜像、相同 DATABASE_URL 和网络路径的受控运行环境中，用应用的 `pianokt_backend.online.connect()` 建立连接并执行 `SHOW extra_float_digits`，预期为 `1`。不要只在 Supabase SQL Editor 中检查另一个连接的值，也不要输出 DATABASE_URL。

```python
from pianokt_backend.online import connect
with connect() as c:
    print(c.execute('show extra_float_digits').fetchone())
```

即使某个连接池覆盖输出精度，本次 Activity/cycle 的浮点一致性判断也在数据库内部完成，不再依赖读回 Python 的浮点文本精度。真实差异仍报 409。

浏览器关闭时不会继续执行后台上传；已落盘的 bundle 会在下次打开应用并登录同一用户后恢复。后台同步失败目前记录在控制台并保留本地数据，尚无全局同步状态面板。Relay/对齐结果生成属于后续验证，不是上述退出和录音提交验收的前置条件。
