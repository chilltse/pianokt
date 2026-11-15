# V3 按需数据导出

设置 DATABASE_URL、DATA_ROOT。数据库必须已应用 migration 024；禁止导出生产数据库到未经批准的本地位置。Exporter 不需要任何 Lake Bucket。

```bash
pianokt-data export-attempts --environment development --cutoff 2026-12-01T00:00:00Z
pianokt-data export-training --environment development --cutoff 2026-12-01T00:00:00Z
pianokt-data export-training --environment development --cutoff 2026-12-01T00:00:00Z --kc-labeler-version 1.2.0 --alignment-version 1.0.0
```

输出打印 manifest，文件位于对应 Gold namespace 的确定性 dataset_version 下。默认 current-online；指定版本时严格选择，遇到同 Attempt 多个匹配 Run 报错。

`--run-selection runs.json` 接受 `{ "attempt-uuid": "alignment-run-id" }`。`--reference-grouping-version` 也支持版本选择。内部 read_committed 可进一步筛选 learner_key / song_id / segment_id。默认只纳入 quality valid 且非 waiting 的结果；不合格结果留在 Silver。

可选 `--output-prefix` 必须精确等于该数据集计算出的 `gold/{type}/{dataset_version}`，不能把 V3 导出到任意旧路径。重试复用 intent 和不可变文件，只有 manifest 最后成功才算发布。

下载使用仓库脚本，它校验 manifest、所有文件和数据库撤回标记：

```bash
python scripts/download_gold.py --dataset-type knowledge_tracing --dataset-version HASH --output-root ./download
python scripts/tse/group_pianokt_by_student.py ./download/gold/knowledge_tracing/HASH ./by_student.json
```

分组脚本仅接受已验证的完整 bundle。保留 UTC learner → visit → attempt → onset 顺序；训练仍须在窗口化前按学生 / 时间划分，不能把相邻同一学生数据随机拆分造成泄露。

## 研究重处理

```bash
pianokt-data reprocess --environment development --attempt-id UUID --alignment-version 1.0.0 --kc-labeler-version 1.2.0 --dry-run
pianokt-data reprocess --environment development --attempt-id UUID --attempt-id ANOTHER_UUID --alignment-version 1.0.0 --kc-labeler-version 1.2.0
```

必须指定显式集合；不会扫描或自动回填历史。Dry run 检查记录、环境、终态和排除政策，不下载 Raw。实际执行校验原始字节并共用 Worker build_silver。输出逐 Attempt 成败及总进度计数；存在失败时退出码 1。重复执行相同输入幂等，可仅重试失败集合。

研究重处理不会替换 READY、成绩或 KT 历史。当前没有自动 Promote 命令；更换在线算法结果需要独立审核操作与新 KT 状态重建，不得直接改 Registry。
