# Challenge UI 与 Product Result 实施记录

## 已实现

保留原 AppBar、Footer、页面容器、背景、标题与插画，替换 Challenge Songs 中间内容。新增 All Songs / Challenged Songs、最近挑战卡片、Featured Songs、四种搜索前置筛选、每页 10 条原 SegmentTable、历史聚合卡片、单一展开详情与当前页双折线图。复用参考文件的 Details 组件。Featured 使用真实曲库前三个条目；没有调用默认 demo 推荐接口，也没有宣称个性化。

新增独立 challenge_results 数据源。客户端在原始 MIDI serialization 前冻结数据，以同一 attempt_id 写入独立 IndexedDB store；记录按用户、前端环境和 API 地址隔离。同步失败保留本地记录，响应丢失可重试。已确认记录仍保留作离线缓存，服务器列表与本地记录按 attempt_id 合并。新 UI 不再读取 aligned accuracy。

ProductResult: attempt_id、song_source、song_id、segment_id、segment_index、midi_sha256、frontend_note_accuracy、completion、is_completed、completion_reason、started_at、ended_at、duration、scoring_version。Accuracy 和 completion 为 0–1，UI 转为百分数。空音符分母准确率为 null。后端派生 is_succeeded；user_id 与 source_environment 来自认证与服务器环境。难度从可信目录读取。POST 冲突返回 409，不覆盖记录。GET 使用 UUID 游标分批读取，不受 Supabase 默认 1000 行上限截断。

Leaderboard 原位置保留，RPC 改为产品结果：完整次数、成功 Segment 数、成功曲目最高目录难度；只统计 production。没有把 0–1 目录难度映射成 1–5。

修复 projections.py 的 user_play_logs INSERT 列清单与实际 schema/value 数不一致；没有给 user_play_logs 增加不必要的字段。

## 文件

- src/features/product-results/{model.ts,store.ts,ChallengeLibrary.tsx,ChallengeHistoryDetails.tsx}
- src/pages/{challenge-songs/page.tsx,challenge/page.tsx,home/Leaderboard.tsx}
- src/features/challenge-history/{api.ts,types.ts}
- backend/pianokt_backend/{product_results.py,api.py,projections.py}
- supabase/migrations/022_product_results.sql
- backend/tests/test_product_results.py、scripts/test_product_results.mjs
- scripts/test_song_discovery.mjs、scripts/test_upload_protocol.mjs（适配真实组件/JSON 导入）

## 迁移与启动

022 依赖现有 auth.users、profiles、官方曲库及 migrations 018–021。应用顺序：先在隔离开发数据库执行迁移并验证权限，再启动新 backend 与前端。未自动执行数据库迁移。

`npm run dev` 或 `npm run build && npm run preview`；前端沿用 VITE_PIANOKT_API_URL，后端使用 PIANOKT_ENV。产品 JSON API 使用现有 bearer 认证。

## 历史兼容

本次不自动回填旧记录，避免错误地推断 completion、Segment 或把 backend accuracy 变成产品成绩。旧科研数据保留。旧记录尚未展示于新产品历史，这是明确的未完成项。后续回填只能依据可信 terminal event、完整身份与 frontend_note_accuracy/client_accuracy_pct，按 attempt_id 幂等导入；未知 completion 保持未知，不能默认完成。

## 验证与限制

类型检查、生产构建、discovery/catalog/activity/uploads/protocol、MIDI/Space/pause 回归已运行。新增聚合测试覆盖 0、1、9、10、11、20、26、100、1001 条及去重/完整与部分成绩。Python 子集（含新增契约测试）206 passed / 140 skipped；完整集合缺 torch。

实际 Chrome 打开本地预览，认证守卫跳转登录页；未获得本地测试登录会话，未绕过认证。桌面/移动历史截图、真实 MIDI 生命周期与离线失败矩阵、数据库集成验收尚未完成，不能宣称全阶段验收通过。

其余待完成：旧历史安全导入；SWR 产品缓存整合（目前使用独立 React 状态和本地事件）；全站后台重试（当前终结时尝试同步，历史页定时/online 重试）；历史分页的数据库快照隔离；数据库层安全与排行榜集成测试；序列化失败后的完整录音清理验证。当前内存聚合会读取完整历史，超大规模用户需改服务器聚合。产品结果是客户端测量，不是防作弊考试评分。

未更改 Alignment、KC、KT 或训练标签。未部署、未推送、未修改生产数据库。
