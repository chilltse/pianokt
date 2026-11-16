# 原始17首曲库统一处理

目标项目：nygtnkuisddcpbgitwhr，用户已明确授权应用023和清理指定范围。工作分支 feature/songs_category_data_pipeline。

身份依据为 catalog_producer_audit.json 的原始 SHA-256。远端16首匹配原始SHA，来源记录为 Sightread 仓库 URL，和审计中乐谱发布页不同；以原始字节、远端来源、song_id 三者审核，不按标题匹配。原始 Gymnopédie No.1 无匹配；同名 Mutopia 来源保留。

本地证据：artifacts/legacy17/dry-run.json、candidate-identities.json、local-review.json。备份（含用户关联记录，不提交Git）：data/legacy17/backup/；其中 deletion-snapshot.json 包含待删精确 song_id 及其关联活动、收藏等，midi/ 保存删前原始对象字节。

标准生产器目录：/Users/tse/Downloads/pianokt_300_auto/output/legacy17_reviewed，仅新增8首，共16个Segment，包含 reference.mid、metadata.json、segment_*.mid。工作副本位于 data/legacy17/catalog。切分边界来自原审计，未重切其他曲目。完整原曲使用原始字节。Greensleeves 1秒静音前缀经音符审核允许，不丢弃任何音符。沿用用户指定的平均音高左右手规则。

同步调用现有 catalog_sync.scan_catalog / sync_song。既有 slug、song_id、许可和来源身份保留，原乐谱出处记录于 metadata.original_score_source_url。删除前插入各环境 song exclusion，防未来Gold导出和reprocess重新引入。只清理无其他完整曲目或Segment引用的精确 MIDI 存储路径。

验证与执行结果见 artifacts/legacy17/execution.json、verification.json（完成后生成）。本次未修改其他曲目内容。运行测试：backend/tests/test_song_import.py，17 passed。

执行结果：已应用023；现有 sync_song 同步8首/16段，实际删除8首（原始Gymnopédie已不存在）、4次关联练习、26个活动事件、2条收藏、1条Product Result和8个无引用目录MIDI对象。其他曲目song_id集合与执行前一致。私有GCS Raw/Silver对象不在此次已验证的Supabase存储清理结果内，未声称已删除；已删除数据库事实与song排除阻止导出重新引入。

最终远端验收通过：8首完整原曲和16个Segment公开下载成功，全部SHA-256匹配；指定删除song_id均不在曲库。详情见 artifacts/legacy17/verification.json。其他来源同名曲目未删除。
