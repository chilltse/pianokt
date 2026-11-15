# 曲库同步实施与实际审计

只读线上审计：65 首、240 个 Segment，独立 full MIDI 表尚不存在。明细 output/catalog_audit/online_before.json。
本地 producer 审计：65 首、288 个 segment 文件，无缺失 reference，无完全相同 reference 内容重复。原始 17 首均需人工确认左右手映射（含单轨或未命名轨道），不自动推断。

新增 023_full_song_catalog.sql，增加独立整曲内容版本与 active 指针；不使用 Segment 0；归档分段保留 UUID 与历史引用。新增 sync-catalog 入口，支持 --environment、--dry-run/--apply、--offline、--report。现有旧导入入口保留。

本地实际验证命令：
`PYTHONPATH=backend .venv/bin/python -m pianokt_backend.cli sync-catalog --catalog-dir /Users/tse/Downloads/pianokt_300_auto --environment development --dry-run --offline --report /tmp/catalog-plan.json`
结果 34 首符合当前校验、31 首待审核（覆盖范围/时长等）。不能将该结果当作上线许可。

生产执行前必须：备份目录元数据、active 指针与分段状态；在隔离 development/test 数据库应用 023 并执行真实同步/重复同步验证；人工确认 17 首左右手与所有拒绝项。生产命令在上述验证后使用相同入口 --environment production --dry-run，再明确确认后 --apply。回滚恢复备份 active 指针及 segment 状态，保留新旧 Storage 对象，不删除历史资源。

尚未完成：producer 目录正式纳入 legacy 审核集、31 首校验拒绝项处理、整曲 Practice 活动身份完整贯通、真实数据库集成/幂等验证、完整回归与浏览器验收。没有可用隔离 PostgreSQL，Docker daemon 未运行。未执行线上迁移或写入。当前为工作中实现，不能宣称全部验收通过。
