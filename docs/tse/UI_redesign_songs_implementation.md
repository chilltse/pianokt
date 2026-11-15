# Segment Discovery 实施记录（2026-11-11 补充修正）

## 完成内容与文件

- `src/features/song-discovery/index.tsx`：两页共用列表、搜索和每页 10 条分页；推荐区域仅显示 `Personalized recommendations coming soon.`；共用收藏按钮提供 Mark / Unmark / Saving、aria-pressed 和 aria-busy。本地曲目仅显示试听，不显示云端收藏。
- `src/features/song-discovery/hooks.ts`：移除推荐查询。收藏使用当前 Auth 用户的 SWR key、optimisticData、rollbackOnError 和 mutate，立即同步 Marked / All / Challenge History；幂等插入和按用户、Segment 删除。每个用户同一时刻只提交一次收藏变更，期间禁用收藏按钮，避免不同 Segment 的并发事务互相回滚。账户切换使用独立缓存并重新读取数据库；旧账户请求不会更新新账户的错误或保存状态。
- `src/features/song-discovery/model.ts`：保留标题、Segment 标题和作曲家的大小写无关搜索；移除不再使用的推荐数据转换。
- `src/features/song-discovery/preview.ts`：复用现有 Player 和数据加载器，官方与本地曲目使用各自 source/id；点击时恢复 AudioContext；支持暂停、继续、结束后重播、下载失败重试、异步竞态保护和订阅清理。
- `src/features/data/index.tsx`：导出已有 fetchSong，让行内试听复用与 Preview Modal 相同的官方 SHA-256 / IndexedDB 加载和本地文件句柄加载路径。
- `src/pages/songs/page.tsx`：向共享列表传入收藏 hook；保留 Folders、本地文件和官方 MIDI 缓存。
- `src/pages/challenge-songs/page.tsx`：有效官方 Segment 的历史行复用收藏/试听，并与下面各区块共享状态。未知官方历史保留原始信息，不推测 Segment、不打开错误的官方预览；本地历史兼容入口保留。最佳对齐准确率与 Status 聚合逻辑未修改。
- `scripts/test_song_discovery.mjs`：增加真实 React DOM + SWR 的交互测试，Supabase 网络传输受控；另有播放器竞态测试及实际 Challenge 页面集成测试。
- `supabase/scripts/verify_segment_marks.sql`：只读查询表、字段、主外键、RLS、Policies 和角色 Grants。
- `supabase/scripts/test_segment_marks_rls.sql`：实际数据库双用户权限验证，整个测试在事务内执行并回滚，不留下测试收藏。

没有新增收藏 FastAPI 接口，没有修改 KT、评分/对齐算法或推荐引擎。

## 数据库部署及实际验证

当前 `.env.local` 的 Supabase 项目与 `backend/.env.local` 中的 Supabase / 数据库项目身份一致。通过管理员数据库连接确认 `piano_segment_marks` 原先不存在后，已成功执行现有 `supabase/migrations/020_segment_marks.sql`，没有重复建表或新增迁移。

随后读取实际数据库并确认：

- 主键为 `(user_id, segment_id)`，两条外键和 `created_at DEFAULT now()` 正常。
- RLS 已启用；SELECT / INSERT / DELETE 的策略仅允许 `auth.uid()` 对应用户。
- authenticated 具有 SELECT / INSERT / DELETE，无 UPDATE 权限。
- anon 无 SELECT / INSERT / UPDATE / DELETE 权限。
- 实际 SET ROLE authenticated + auth.uid() 双用户事务测试通过：自己的幂等插入、读取和删除成功；另一用户无法读取、删除或以对方身份插入。两人的相同 Segment 收藏互不影响。
- 验证事务已 ROLLBACK，无测试收藏残留。

这是当前配置项目的部署结果；其他环境需要先检查再部署。若使用 Supabase SQL Editor，可分别执行上述 SQL 文件。使用 psql 时先在本地安全设置目标 DATABASE_URL，然后：

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/scripts/verify_segment_marks.sql
# 仅当表不存在且 018/019 已应用时执行：
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/migrations/020_segment_marks.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f supabase/scripts/test_segment_marks_rls.sql
```

权限测试需要管理员连接、至少两个已有 Auth 用户以及一个双方都未收藏的 Segment；缺少条件会明确失败，不会伪报通过。020 通过 SQL 直接执行，不代表执行过整个项目的 `supabase db push`。

## 自动化验证

- `npm run check-types`
- `npm run build`
- `npm run test:discovery`
- `npm run test:catalog`
- `npm run test:activity`
- `npm run test:uploads`
- `npm run test:protocol`

Discovery 测试覆盖点击后服务器返回前立即变心、Marked 同步、保存成功后用空 SWR 缓存重新加载、添加/取消失败回滚、同 Song 的两个 Segment 独立收藏、重复点击、退出登录、账户切换和旧请求完成、搜索先于分页、搜索重置页码、目录缩小页码有效、搜索不影响收藏区、播放按钮不触发行选择、官方/本地身份、暂停/继续/结束重播、竞态、错误重试、卸载订阅清理、历史身份及打开弹窗停止试听。测试拒绝任何非收藏表查询，确保不请求推荐数据。

React/SWR 测试使用受控 Supabase 传输；真实数据库权限由上述 SQL 集成测试独立验证，不以 mock 结果代替。

## 本地手动验收与发布

1. `npm run dev`，打开输出的本地地址并进入 `/songs`。未登录时确认可搜索、翻页和试听；点击收藏仅提示登录；推荐只显示占位文案。
2. 登录后收藏同一 Song 的两个 Segment，立即查看实心和 Marked 区；取消其中一个，另一个应保留。刷新并切换至 Challenge 页面，验证状态一致。
3. DevTools 阻断收藏表请求，再添加/取消收藏，确认立即变化后回滚且出现非阻塞提示；恢复网络后重试。切换账户确认不会看到前一账户的收藏。
4. 在 All / Marked / Challenge History 快速切换试听，暂停后继续、播放结束后重播，打开预览或离开页面后确认声音停止。通过 Folders 导入本地 MIDI，确认可试听且没有收藏按钮。
5. 在浏览器验证小屏布局、首次播放音频授权和实际扬声器/MIDI 输出。音频听感、真实双设备登录刷新和各浏览器文件系统授权仍需人工验收；自动化测试不声称覆盖真实音频硬件。
6. 发布前端 `npm run build` 产物。当前配置数据库的 020 已部署；若生产指向其他 Supabase 项目，先按上面的只读 SQL 检查并按需应用 020。前端尚未发布。

推荐接入按本次要求暂不实施。无其他待实现的非推荐功能；剩余事项为前端发布和上述真实浏览器/设备验收。
