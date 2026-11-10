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