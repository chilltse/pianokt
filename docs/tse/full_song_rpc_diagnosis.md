# Entire Song RPC 本地诊断

已只读验证当前环境：前后端连接同一个 Supabase 项目，后端 PIANOKT_ENV=development。Segment RPC 返回 200（240 条）；整曲 RPC 返回 PGRST202。直接查询 pg_proc 仅存在无参数 piano_public_catalog；to_regclass(public.piano_song_full_midis) 返回 null。

根因为数据库未具备 migration 023 的整曲 schema，不是前端签名错误或单纯 cache。development 是后端数据标签，不证明该 Supabase 项目是独立开发数据库。本次未执行远端 SQL。

在确认目标为允许迁移的开发项目后，应用 supabase/migrations/023_full_song_catalog.sql。该 migration 还包含完整 MIDI 表、active_full_midi_sha256 及 Segment 身份约束，不能只创建函数或用 Segment RPC 替代。迁移内已加入 NOTIFY pgrst, 'reload schema'。如平台仍未刷新，可另行执行同一 NOTIFY。

随后调用 piano_public_full_song_catalog()，应返回 JSON 数组（尚未导入完整 MIDI 时允许为空）。完整资源需通过现有 sync-catalog 导入并关联同一个 song_id；不得将 Segment 冒充 Entire Song。Challenge 继续使用 piano_public_catalog。

023 不是重复执行脚本；若其他环境已部分执行，先检查表、列、约束和函数，不要盲目重跑。无需修改历史演奏或 V3 Pipeline。

## 2026-11-16：023 应用后的实际权限故障

本地浏览器复现 Entire Song → Unable to load。只读远端检查：本地前后端均指向 nygtnkuisddcpbgitwhr；Segment RPC 200，整曲 RPC 401/code 42501。函数和完整曲目表都存在。根因为023的 security invoker 需要读取018明确禁止 anon/authenticated 直接访问的 piano_song_catalog，而非曲目缺失或缓存。

已在该目标项目应用026：只将固定查询的整曲公开函数改为 security definer，沿用空 search_path、全限定表名及 published/active过滤；基础表权限不变。公开RPC修复后返回8首。

真实 Chrome localhost:5173 验收：Für Elise 独立三角试听显示 Pause 且浏览器音频播放，Entire Song弹窗显示02:28/1047 notes/2 tracks，Play Now进入 full:4e3f6831-14b5-4359-82a4-7dca6ec9b1f0 的Practice并启动播放。Greensleeves同样通过独立试听、弹窗试听、Play Now到 full:7e629197-f6b7-4295-9c90-160f440abb6d；显示01:13/256 notes，实际Practice进度推进至00:31。song_id与分段父身份一致。

刷新页面清除了SWR旧错误状态，未清理IndexedDB；已缓存MIDI正常工作。未改Challenge逻辑，未上传、切分或删除任何曲目。新增test_full_song_rpc.py使用真实SQL与anon/authenticated角色覆盖原故障、修复后公开读取、隐藏草稿/非active版本及基础表仍不可直接访问。
