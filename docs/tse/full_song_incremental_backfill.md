# 已发布曲目完整原曲增量补齐

目标：现有Supabase项目 nygtnkuisddcpbgitwhr。仅补齐 piano_song_full_midis 与 active_full_midi_sha256，不改 song_id、Segment 身份/文件或学生历史。

审计57首发布曲目：8首已有原曲，48首可由生产器原始内容SHA派生slug（pianokt-{sha前16位}）且来源URL一致，或远端全部Segment内容SHA及来源一致，唯一匹配本地reference。标题不用于身份判定。Test tittle No.1（dcd1cf72-14fb-4513-9d59-54499187c101）未匹配，保留并报告。

执行工具：backend/diagnostics/backfill_full_songs.py。默认dry-run；--apply前保存before.json。已有Storage对象先读取校验后复用，只有缺失对象才上传；公开下载SHA校验后才提交完整曲目记录和指针。排除表中曲目不补齐。对全部Segment表执行前后严格相等检查。

证据位于data/full-song-backfill：dry-run.json、before.json、apply.json、verification.json、downloads/。目录不提交Git。公开RPC和下载验证后，用scripts/test_full_song_downloads.mjs调用真实前端parseMidi逐个解析，结合Player回归与本地浏览器抽样确认播放链路。

## 实际完成结果

用户确认来源目录为 output/intermediate_advanced/accepted 与 output/accepted。48首增量原曲分别来自前者18首、后者30首，均复用现成reference.mid；另8首原有整曲未改动。中途网络断连后按数据库缺失状态幂等续跑，最终56首有active完整原曲。

远端公开RPC返回56首；56份公开下载SHA-256及Segment父song_id均验证通过。scripts/test_full_song_downloads.mjs使用真实前端parseMidi解析56首全部成功。Segment身份和MIDI表执行前后严格相等，未改活动/练习记录，未新增Song或Segment，未恢复排除曲目。

真实Chrome抽样新增的Traditioner af Swenska Folk-Dansar：完整预览01:04、326音符、2轨；Play Now进入full:a2cc1638-0ed0-4c80-ba8f-4fc08c2e44ed并实际播放到01:04终点。全部56首进行了下载与前端解析验证，未逐首在浏览器手工播放。Player pause/resume和SongDiscovery/缓存回归通过。

唯一未匹配：Test tittle No. 1，song_id dcd1cf72-14fb-4513-9d59-54499187c101。没有本地内容身份或来源证明，仍保留其曲目和Segments，Entire Song继续Unavailable。需要其原始reference.mid及来源证明才能补齐，不能用Segment冒充。

来源逐首清单：data/full-song-backfill/source-provenance.json。执行前快照before.json保持不覆盖。未执行重新切分、修改其他字段或清理数据。
