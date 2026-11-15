# PianoKT Song Discovery UI — Segment-first Implementation

请在当前 `feature/3_layer_kc` 分支完成前端曲目发现与管理功能改造。

## 核心原则：Everything is Segment-level

PianoKT 当前所有学习交互均以 **Segment 为最小单位**。

- `segment_id`：推荐、收藏、练习、挑战、历史记录及试听的唯一业务标识。
- `song_id`：只用于保留父级歌曲归属、标题与作曲家等元数据，未来可能用于用户偏好推荐。
- `midi_sha256`：用于 MIDI 内容版本识别与缓存，不用于用户收藏或练习记录的主身份。

同一首 Song 下不同 Segment 必须被视为相互独立的练习对象。

例如：用户收藏 Segment 3，不意味着收藏 Segment 1、2、4；推荐 Segment 3，也不意味着推荐整首 Song。

不要合并不同 Segment 的收藏、挑战结果或练习进度。

## 1. Challenge Songs 页面

修改 `src/pages/challenge-songs/page.tsx`。

页面从上到下展示：

### Challenged Songs

保留现有挑战历史、Best Accuracy 和 Status。

在标题后增加辅助提示：

`(Alignment and scoring may take up to 5 minutes.)`

确保挑战历史按 `segment_id` 独立展示与聚合。对于旧数据没有 `segment_id` 的情况，保留现有兼容展示，但不要根据标题或文件名猜测对应的官方 Segment。

### Recommended Songs

这是独立的个性化 Segment 推荐区域。

- 从 Supabase 中读取当前用户最近一组有效的真实推荐结果。
- 优先复用 `piano_recommendations` 及现有数据访问方式。
- 推荐项以 `segment_id` 标识。
- 只展示当前仍可播放的已发布 Segment。
- 暂时没有真实推荐记录时，显示 `No personalized recommendations yet.`
- 不使用 DemoRecommender、随机推荐或未挑战过的 Segment 作为替代。
- 本次不修改 PyKT 推理、Sweet Spot、MC Rollout 和 Gain 算法。

### Marked Songs

展示当前用户收藏的 Segment。

- 每个 Segment 独立收藏与取消收藏。
- 不同 Segment 即使属于同一首 Song，也必须具有独立的 ❤️ 状态。
- 收藏状态在 Challenge 与 Practice 两个页面保持一致。
- 收藏数据从 Supabase 读取和持久化。
- 支持从列表打开对应 Segment 的挑战预览。

### All Songs

展示所有可用 Segment。

- 每行展示一个 Segment。
- 使用现有 `useSongManifest()` 和 Supabase 曲库数据。
- 支持按 Song Title、Composer 和 Segment Title 模糊搜索。
- 搜索先于分页执行。
- 每页最多 10 个 Segment。
- 显示当前页、总页数及总结果数。
- 搜索变化时重置页码。
- 支持收藏与 MIDI 行内试听。

## 2. Practice a Song 页面

修改 `src/pages/songs/page.tsx`。

从上到下展示：

1. Recommended Songs
2. Marked Songs
3. All Songs

所有区块同样以 `segment_id` 为单位。

复用 Challenge 页面的推荐、收藏、列表、分页和试听组件。

区别在于：

- Practice 页面点击条目后进入 `SongPreviewModal(mode="play")`。
- Challenge 页面点击条目后进入 `SongPreviewModal(mode="challenge")`。

保留 Practice 页面的本地 MIDI 导入、Folders、已有曲库缓存功能。未登录用户仍可浏览和试听曲库；个性化推荐与收藏需要登录。

## 3. Supabase Segment 收藏

检查现有 schema 是否已经存在合适的用户收藏表。

如果不存在，新增向后兼容的 migration，创建：

`public.piano_segment_marks`

建议字段：

- `user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`
- `segment_id uuid NOT NULL REFERENCES public.piano_song_segments(segment_id)`
- `created_at timestamptz NOT NULL DEFAULT now()`
- `PRIMARY KEY (user_id, segment_id)`

启用 Row Level Security：

- 仅允许用户 SELECT 自己的收藏。
- 仅允许用户 INSERT 自己的收藏。
- 仅允许用户 DELETE 自己的收藏。
- 不允许用户访问、修改其他用户的收藏。

通过当前 Supabase Auth 身份确定 `user_id`，不要使用路由参数作为数据库用户身份。

收藏 API 需要支持幂等添加与删除、重复点击防护、加载状态、错误反馈及失败后的状态恢复。

未登录状态下可以显示 ❤️，但点击后应提示登录，不应伪造云端收藏成功。

## 4. MIDI 行内试听

在每个 Segment 的最右边添加 Play/Pause 按钮。

- 播放当前 Segment 的 MIDI，而不是其 Parent Song。
- 点击按钮不触发行点击，也不打开 Preview Modal。
- 同时只能播放一个 Segment。
- 再次点击可以暂停或恢复。
- 切换 Segment 时停止前一个。
- 离开页面、打开 Preview Modal 或开始 Challenge/Practice 时停止试听。
- 支持 Loading、Error 和 Retry。
- 防止异步 MIDI 加载的竞态问题。
- 复用现有 MIDI Parser、Player、Synth、`loadOfficialMidi()` 和 IndexedDB 缓存。
- 不引入新音频引擎。

## 5. 共享组件和身份一致性

建议抽取：

- `SongSection`
- `SegmentTable`
- `SegmentPreviewButton`
- `useSegmentMarks`
- `useSegmentRecommendations`

官方 Segment 的稳定身份必须使用 `segment_id`；列表、选择状态、收藏状态和 React key 不得仅使用 `song_id`。

对于本地 MIDI，保留已有 `(source, id)` 逻辑，不要伪造官方 `segment_id`，也不要让本地文件意外共享官方 Segment 的收藏记录。

所有与数据库有关的操作优先复用现有 Supabase Client 和 RLS，不引入新的状态管理框架。

## 6. 验收标准

至少覆盖以下情况：

1. 两个页面均正确展示 Recommended、Marked 和 All Songs。
2. 同一 Song 下不同 Segment 分别显示、分别打开、分别收藏。
3. 收藏 Segment A 不影响同一 Song 下的 Segment B。
4. 收藏在两个页面同步，并可在刷新后恢复。
5. 不同用户的收藏数据受到 RLS 隔离。
6. All Songs 搜索与分页正确，每页最多 10 个 Segment。
7. 每个 Segment 的试听播放的是正确 MIDI。
8. 同时最多一首试听，快速切换不会出现播放竞态。
9. 推荐为空时不显示随机或 Demo 推荐。
10. Challenge 历史按 Segment 独立聚合，旧记录继续正常展示。
11. 原有 Practice、Challenge、Folders、MIDI 缓存和 Preview Modal 功能不回归。

## 7. 实施约束

- 不修改 KT 模型或推荐算法。
- 不重构 MIDI 对齐、评分与练习记录的业务流程。
- 不重建官方曲库。
- 不删除已有数据库数据。
- 优先复用已有组件和服务。
- 保持现有 UI 风格和响应式布局。

完成后运行 TypeScript 检查、构建与相关测试，并报告修改文件、migration、测试结果及需要执行的部署步骤。

请直接完成代码实现，不要只输出设计方案。



-----------补充修正
# PianoKT — 完成 Segment Discovery 与收藏系统

请基于当前 `feature/3_layer_kc` 分支的最新代码直接实施修改。

**目标：完成除 Recommended Songs 推荐功能之外的所有 Song Discovery 功能，包括前端交互、Supabase 持久化、必要的后端支持和自动化测试。**

要求架构尽量简洁，与当前代码一致。不要重新设计已有模块或引入新的大型抽象。

## 一、先检查现有实现

重点检查：

- `src/features/song-discovery/index.tsx`
- `src/features/song-discovery/hooks.ts`
- `src/features/song-discovery/model.ts`
- `src/features/song-discovery/preview.ts`
- `src/pages/songs/page.tsx`
- `src/pages/challenge-songs/page.tsx`
- `supabase/migrations/020_segment_marks.sql`
- `scripts/test_song_discovery.mjs`

当前已经有 `SongDiscovery`、`SegmentTable`、`useSegmentMarks` 和 `useSegmentPreview`。

请在已有代码上增量完善，不要平行开发另一套系统。

## 二、Recommended Songs 暂时不实现

两个页面都保留 Recommended Songs 区域，但目前只显示：

`Personalized recommendations coming soon.`

本次不读取推荐数据、不调用推荐 API，也不实现任何推荐计算或 fallback。

不要修改现有 PyKT / Recommendation Engine 的后端逻辑，未来再单独接入。

## 三、完成 ❤️ Segment 收藏功能

所有收藏均以 `segment_id` 为单位，不能以 `song_id` 或 `midi_sha256` 作为收藏身份。

### 用户交互

1. 未收藏时显示空心红色 ❤️。
2. 点击后立即变为实心红色 ❤️，无需等待服务器响应。
3. 同时通过 Supabase 保存收藏。
4. 保存成功后维持实心。
5. 保存失败时恢复原来的状态，并显示非阻塞错误提示。
6. 再次点击立即恢复空心，并删除 Supabase 对应记录。
7. Marked Songs 区域立即反映增加或删除的 Segment。
8. 相同 Segment 在不同区域必须共享同一个收藏状态。
9. 防止快速重复点击引起请求竞态或状态错乱。
10. 收藏状态刷新页面、重新登录和更换设备后均可通过 Supabase 恢复。

优先使用现有 SWR 的 optimistic update、rollbackOnError 和 mutate 能力，不引入 Redux、Zustand 等状态管理框架。

完善按钮的 `aria-label`、`aria-pressed` 和保存中状态。

### Supabase

已存在 `020_segment_marks.sql`，请先检查，不要重复创建表。

目标结构仍然是：

`piano_segment_marks(user_id, segment_id, created_at)`

要求：

- `(user_id, segment_id)` 唯一。
- 保留外键。
- RLS 启用且仅允许用户管理自己的记录。
- 插入采用幂等策略。
- 删除只影响当前用户对应的 Segment。
- 不允许通过前端指定其他用户的身份绕过 RLS。
- 登录状态改变后正确清理或切换 SWR 用户缓存。

请提供 SQL 验证方式，确认数据库表、RLS、Grants 和 Policies 的实际状态。

如果数据库迁移尚未应用，请执行必要的安全部署步骤；如果当前环境没有数据库部署权限，请明确给出需要执行的迁移命令，不要声称已经部署成功。

不要创建新的收藏 FastAPI 接口，除非现有 Supabase Client + RLS 确实无法完成需求。

## 四、完善两个页面

### Challenge Songs

保留：

- Challenged Songs
- Recommended Songs（占位）
- Marked Songs
- All Songs

保留现有 Challenge History 的最佳对齐准确率及状态逻辑。

继续展示：

`(Alignment and scoring may take up to 5 minutes.)`

确保每个具有有效官方 `segment_id` 的挑战历史条目也能显示收藏按钮和试听按钮，与其他区块共享状态。

对于无法解析官方 Segment 身份的历史记录，继续显示原有历史信息，不猜测 Segment 身份，也不创建错误收藏。

### Practice a Song

保留：

- Recommended Songs（占位）
- Marked Songs
- All Songs
- Folders
- 本地 MIDI 导入
- MIDI 缓存

确保收藏、试听与 Challenge 页面行为一致。

未登录用户仍能浏览和试听，但云端收藏需要登录。

## 五、All Songs 搜索与分页

保留现有 `searchSegments()`。

完成并验证：

- 每行表示一个独立 Segment。
- 搜索支持 Song Title、Segment Title、Composer。
- 搜索大小写不敏感。
- 先过滤，再分页。
- 每页最多 10 个 Segment。
- 搜索变化后返回第一页。
- 总数和页码准确。
- 目录更新后不会出现无效页码。
- 加载、空结果和请求失败有明确反馈。

如果现有代码已满足要求，不要为了重构而重构。

## 六、MIDI 行内试听

继续使用 `useSegmentPreview()`。

必须保证：

- 点击播放按钮不触发行选择或页面跳转。
- 播放正确 Segment 的 MIDI。
- 同时只能有一个列表试听。
- 切换 Segment 时停止旧播放器。
- 点击 Pause 可以暂停，再次点击可以恢复。
- 播放结束时 UI 状态正确恢复。
- 打开 SongPreviewModal 或进入练习/挑战后停止行内试听。
- 页面卸载时清理 Player、订阅与相关资源。
- 异步加载 MIDI 时支持取消或防止旧请求覆盖新状态。
- 下载失败时显示错误并允许重试。
- 官方 MIDI 继续使用已有 SHA-256 校验与 IndexedDB 缓存。

当前列表试听只支持官方 Segment。请在不破坏现有架构的前提下，让本地导入 MIDI 也能使用列表试听；本地 MIDI 没有官方 segment_id 时，不允许伪造云端收藏身份。

优先复用现有 Player、MIDI Parser 和数据访问代码。

不要引入新的 MIDI 播放器库。

## 七、保持架构简单

继续保留现有结构：

- `index.tsx`：共享列表与 UI
- `hooks.ts`：收藏数据及相关 hooks
- `model.ts`：纯数据转换、搜索、过滤
- `preview.ts`：行内 MIDI 试听

除确有必要，不新增更多模块。

前端直接通过 Supabase Client 调用受 RLS 保护的收藏表，不额外增加无意义的 Cloud Run API 往返。

保留当前的 SongMetadata、官方目录读取、React Router 和页面组织方式。

不修改现有 PyKT、对齐算法、训练数据、曲库版本身份或推荐引擎。

## 八、完整验收

请补充必要的自动化测试，并执行：

- `npm run check-types`
- `npm run build`
- `npm run test:discovery`
- 其他相关测试

同时覆盖以下场景：

1. 空心 ❤️ 点击后立即成为实心。
2. Supabase 写入成功后刷新仍然实心。
3. 取消收藏后立即变为空心，数据库记录被删除。
4. 保存失败后 UI 正确回滚。
5. 两个页面和不同 Section 共享收藏状态。
6. 同一 Song 下多个 Segment 可分别收藏。
7. 不同用户的收藏彼此隔离。
8. 未登录用户无法写入云端收藏。
9. 搜索、分页与收藏筛选不会互相干扰。
10. MIDI 试听状态正确，不会出现重复播放或异步竞态。
11. Challenge 历史保持 Segment-level 的独立性。
12. Recommended Songs 只显示占位，不请求真实或演示推荐。

如有必要，给出可在实际 Supabase 项目执行的 RLS 验证 SQL，不能只依靠 mock 测试证明数据库权限正确。

## 九、完成标准

这次不是仅完成前端外观，而是把非推荐功能做成一个完整、可持久化、可测试的闭环。

完成后请报告：

- 修改了哪些文件
- 数据库是否需要部署迁移
- 哪些功能已经真实验证
- 哪些测试通过或失败
- 如何在本地进行手动验收
- 哪些事项需要生产环境部署
- 还有什么尚未完成

**请直接实施修改，不要停留在方案设计。尽量减少代码量、组件数量和无必要的后端接口，保持与当前工程架构一致。**