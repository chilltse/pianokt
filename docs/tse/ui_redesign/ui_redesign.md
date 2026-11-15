# PianoKT Challenge System — UI Redesign & Product–Research Decoupling

请在 当前工程完成：

开始前，完整检查当前分支的实际实现、数据库 Schema、路由、Challenge 生命周期、上传恢复机制、曲库身份、推荐接口及相关测试。以下文件路径仅作为检查入口，应以实际工程为准。

如果存在 `docs/tse/ui_redesign/pianokt-challenge-reference 2/index.html`、`docs/tse/ui_redesign/pianokt-challenge-reference 2/ChallengeHistoryDetails.tsx` 和 `docs/tse/ui_redesign/pianokt-challenge-reference 2/IMPLEMENTATION.md`，请同时阅读，并作为视觉参考。

## 1. 总体目标

PianoKT 的产品层主要服务于钢琴学习者，提供即时反馈、练习参与感、成长记录和持续练习的动力。

科研层负责录音采集、Alignment、KC Labeling、Knowledge Tracing 和 Recommendation。

两者必须解耦。

用户完成的挑战、前端正确率、完成度和挑战成功状态，不应依赖 MIDI 上传成功、Worker 执行、Alignment 完成或 KC 标注成功。

本次重构的目标不是重新设计整个 PianoKT 网站，而是在原有设计语言和工程架构基础上，改善 Take a challenge 页面以及产品成绩的数据链路。

## 2. 严格的 UI 修改范围

只重新设计 `/challenge-songs` 页面中间的内容区域，以及必要的产品成绩相关组件。

必须原样保留：

- 原有 AppBar。
- 原有 MarketingFooter。
- 原有 PianoKT Logo。
- 原有网站背景及纸张纹理。
- 原有字体、字号体系、页面宽度及整体间距。
- 原有 Take a challenge 标题。
- 原有 `challenge-anime.png` 插画。
- 原有全站导航和路由。
- 原有主页布局。
- 原有主页中的 Leaderboard 模块位置及整体视觉样式。
- 原有 Practice a song、Free play、About 页面。
- 原有 Challenge Player 和 SongPreviewModal 的交互方式。

不得重新设计全站导航，不得建立新的 Dashboard，不得将 Leaderboard 移动到独立页面。

继续使用当前工程的 React、TypeScript、Tailwind CSS、Lucide Icons、SWR 和已有组件。

能够复用现有组件的地方必须复用，而不是重新实现。

禁止加入新的欢迎页面、营销文案、无意义装饰、渐变背景、通用钢琴图片或额外的 Challenge Selection 面板。

整体风格保持原有 PianoKT 的简洁、温暖、浅色、低视觉负担的设计语言。

## 3. Challenge 页面布局

在原有 Take a challenge 标题、说明和插画下方增加两个内部 Tab：

- All Songs
- Challenged Songs

默认打开 All Songs。

这两个 Tab 仅属于 `/challenge-songs` 页面，不是全站一级导航。

切换 Tab 时应保持合理的页面状态，避免重复加载、闪烁或丢失已经读取的曲库和用户记录。

All Songs 面向用户下一步练习的选择。

Challenged Songs 面向用户历史成长和练习记录。

## 4. All Songs 页面

All Songs Tab 从上到下只包含三个区域：

1. Continue Practicing
2. Recommended for You
3. All Songs

不要添加 Ready to play、Explore Songs 或其他额外的标题和宣传文案。

### 4.1 Continue Practicing

仅展示用户最近一次实际挑战过的 Segment。

这里的最近一次挑战不要求录音成功上传，也不要求后端 Alignment 完成。

使用简洁白色卡片，沿用原有 amber 边框、圆角及页面样式。

显示：

- 曲目标题及 Segment 标题。
- Composer。
- Duration。
- Difficulty（如果曲库具有真实且可解释的难度数据）。
- 当前历史最佳状态。
- Best Completion 进度条及百分比。
- Attempts 次数。
- Last Played 时间。
- 原有收藏按钮。
- Try Again 或 Replay 按钮。

参考布局：

```text
Continue Practicing

┌─────────────────────────────────────────────┐
│ Clair de Lune · Part 2          In Progress │
│ Claude Debussy · Level 4 · 0:52           ♡ │
│                                             │
│ Best Completion                         82% │
│ ████████████████░░░░                        │
│                                             │
│ 5 attempts · Last played today    Try Again │
└─────────────────────────────────────────────┘
```

按钮规则：

- In Progress：Try Again。
- Completed：Try Again。
- Succeeded：Replay。

点击按钮使用现有 Challenge 启动流程，必要时打开原有 SongPreviewModal，保留 Challenge 设置和 MIDI 设备安全机制。

不要创建新的 Challenge Selection 页面或面板。

Continue Practicing 表示重新挑战该 Segment，而不是在上次中断位置继续原来的 Attempt。

没有挑战历史的新用户隐藏整个 Continue Practicing 区域，不展示无意义的空卡片。

如果最近挑战的曲目已经无法从曲库取得，显示合理的不可用状态，不得错误关联其他 Segment。

### 4.2 Recommended for You

保留简洁的推荐卡片区域，默认展示最多三首推荐曲目。

桌面端横向排列，移动端响应式布局。

每张卡片显示：

- 曲目名称。
- Composer。
- Difficulty（如有真实数据）。
- Duration。
- 收藏按钮。
- Challenge 按钮。

不使用大型图片、随机插画或复杂装饰。可以使用简单的音乐图标和浅 amber 区域。

检查当前 `/recommendations` API、推荐策略配置及现有 Demo 模式。

不得修改推荐模型、候选筛选、KT 推理或 Monte Carlo Rollout 算法。

仅接入当前明确允许使用的推荐接口。

如果当前推荐并非真正的个性化模型结果，应明确使用 Featured Songs 或其他准确标题，不得伪造个性化推荐理由。

推荐接口失败时不能阻塞 All Songs、Continue Practicing 或其他正常功能。

推荐服务加载状态必须局限于推荐区域。

### 4.3 All Songs 曲库表格

这是本次设计中必须保持原样的部分。

不要将曲库重新设计成卡片列表，不要给每首歌增加进度条，不要加入大量新图标。

检查并优先复用：

- `src/features/song-discovery/index.tsx`
- `SegmentTable`
- `SegmentMarkButton`
- `SegmentPreviewButton`
- `useSegmentMarks`
- `useSegmentPreview`
- `searchSegments`
- 现有曲库缓存及分页逻辑。

保留原有表格结构：

```text
All Songs

[ Search Song Title, Composer or Segment Title ]

All    ♥ Favorites (2)    Not Tried    Challenged

Title / Composer                    Length

Clair de Lune · Part 2                0:52    ♥    ▷
Claude Debussy

Für Elise · Part 1                    0:48    ♡    ▷
Ludwig van Beethoven

Prelude in C Major                    0:56    ♡    ▷
Johann Sebastian Bach

Page 1 of 30 · 300 results       Previous    Next
```

表格只保留原有：

- Title / Composer。
- Length。
- 收藏按钮。
- 试听按钮。

不要加入 Best Accuracy、Completion、Status、Attempts 等额外列。

这些信息应当留在 Challenged Songs Tab。

**试听按钮必须直接复用原有 SegmentPreviewButton。**

沿用现有 Lucide `Play`、`Pause`、`LoaderCircle`、`RotateCcw` 图标及按钮样式。

播放按钮必须是原来的独立三角形图标，不能使用圆圈包围三角形，不能使用新的圆形播放按钮，也不能改变原有试听行为。

收藏按钮也必须直接复用 SegmentMarkButton，保留当前实心和空心爱心样式、颜色、乐观更新、账户隔离及错误回滚逻辑。

点击曲目标题或表格行，沿用原有 Challenge Preview 入口。

点击收藏或试听时，不得意外触发 Challenge Preview。

### 4.4 搜索与筛选

在原有搜索框下方增加四个简洁的筛选选项：

- All
- Favorites (N)
- Not Tried
- Challenged

默认选择 All。

定义：

- All：全部可用官方 Segment。
- Favorites：用户收藏的 Segment。
- Not Tried：用户从未实际开始挑战的 Segment。
- Challenged：用户至少实际开始过一次挑战的 Segment，包括提前退出。

仅打开预览、取消 Countdown、尚未实际开始演奏，不算 Challenged。

Favorites 不再作为单独的大型曲目列表展示。

Favorites 的数量和收藏状态必须与 Continue Practicing、Recommended for You、Challenged Songs 共享同一数据来源。

搜索和筛选必须先于分页。

搜索或筛选条件改变后返回第一页。

### 4.5 分页

保留原工程的分页样式：

```text
Page 1 of 30 · 300 results       Previous    Next
```

每页最多10个 Segment。

正确处理：

- 空结果。
- 最后一页不足10条。
- 搜索或筛选后页数减少。
- 取消收藏后当前页面为空。
- 曲库更新。
- 用户账户切换。
- 未同步成绩导致挑战状态变化。

不得因为改造 Challenge 页面而改变 `/songs` 中原有 Practice a song 的布局。

如需修改共享 SongDiscovery 组件，使用明确的模式参数或拆分公共组件，保证 Practice a song 原有行为不受影响。

## 5. Challenged Songs 页面

Challenged Songs 是个人成长档案。

采用已经确定的卡片式布局，而不是当前只有 Best Accuracy 和 Success 的历史表格。

顶部显示：

- Challenged：挑战过的不同 Segment 数量。
- Succeeded：曾成功挑战的不同 Segment 数量。
- Full Challenges：完整挑战的累计次数。

不要展示 Success Rate，避免鼓励用户只选择简单曲目。

顶部提供筛选：

- All
- In Progress
- Completed
- Succeeded

提供排序：

- Recently played（默认）。
- Best Completion。
- Best Accuracy。
- Most Attempts。

可以对主曲目列表进行每页10个 Segment 的分页，以保证大量历史曲目时页面依然简洁。

顶部统计必须基于完整用户历史，而非当前筛选结果或当前分页。

### 5.1 曲目卡片

每个 Segment 对应一张卡片。

参考布局：

```text
┌──────────────────────────────────────────────┐
│ Für Elise · Part 1              ✓ Succeeded  │
│ Ludwig van Beethoven · Level 3               │
│                                              │
│ Best Completion          Best Accuracy       │
│ ███████████████ 100%           96.2%          │
│                                              │
│ 26 attempts · Last played today              │
│                      Details ▾    ♡   ▷ Replay│
└──────────────────────────────────────────────┘
```

卡片信息包括：

- Title 和 Composer。
- Difficulty（可用时）。
- 历史最佳状态。
- Best Completion。
- Best Accuracy。
- Attempts。
- Last Played。
- Details。
- 原有收藏和试听入口（适用时）。
- Try Again 或 Replay。

使用现有白色卡片、浅 amber 边框、适当圆角和原有字体体系。

Completion 使用进度条。

Accuracy 使用明确的百分比数字，不需要第二个进度条。

状态使用低饱和度颜色：

- In Progress：暖金色。
- Completed：蓝灰色。
- Succeeded：绿色。

不要让每张卡片具有大量彩色区域。

### 5.2 状态定义

统一使用以下英文状态：

- In Progress
- Completed
- Succeeded

不使用 Cleared。

状态是一个 Segment 基于历史所有有效产品挑战记录得到的最高成就。

状态优先级：

```text
Succeeded > Completed > In Progress
```

规则：

In Progress：至少开始过一次真实挑战，但从未完整完成。

Completed：至少完整完成一次，但从未达到成功阈值。

Succeeded：至少存在一次完整完成且前端 Note-level Accuracy ≥ 90% 的挑战。

用户成功挑战后再次挑战失败，不应导致该 Segment 的历史状态下降。

成功阈值应复用已有的 Challenge Success 规则，保持统一定义，不要在不同组件中分别硬编码。

### 5.3 Best Completion

Best Completion 是该 Segment 所有有效产品挑战记录中的最高完成度。

完成度表示用户在本次挑战中实际推进到的 Segment 位置，而不是弹对的音符比例，也不是单纯的演奏耗时。

使用真实的 Challenge assigned range 和 song-time transport 计算。

对于非零起点的 Segment，必须减去 range start，并以 assigned range 的实际长度归一化。

不得把整首原始歌曲的 duration 直接作为所有 Segment 的分母。

暂停时间、等待时间不得增加 Completion。

应检查当前 Player 的跳转、循环、速度变化、暂停及重播语义，避免通过 seek 或重复播放人为制造错误完成度。

正常完成整个合法 Challenge 的 Completion 为100%。

提前退出不得仅仅因为四舍五入显示为100%就被判定为完整完成。

Completion 数值必须限制在合法范围内。

### 5.4 Best Accuracy

Best Accuracy 只统计完整完成的 Challenge。

```text
BestAccuracy =
    max(frontend_note_accuracy)
    over completed attempts
```

若从未完整完成，则显示 `—`，不能使用0%代替缺失结果。

例如：

```text
Attempt 1: Completion 20%, Accuracy 100%
Attempt 2: Completion 50%, Accuracy 95%
Attempt 3: Completion 100%, Accuracy 85%
```

最终卡片：

```text
Best Completion: 100%
Best Accuracy: 85%
Status: Completed
```

不能因为第一次只弹20%但是 Accuracy 为100%，就将其判定为 Succeeded。

Succeeded 必须来自同一次完整且正确率达到阈值的挑战。

Best Completion、Best Accuracy、Status 必须基于全部历史计算，不受 Details 分页影响。

不要修改当前前端 Note-level Accuracy 的计算算法。

继续使用：

```text
(Perfect + Good) / (Perfect + Good + Miss + Error)
```

检查现有实现及边界情况。不要将空分母、缺失数据或无效成绩转换成虚假的0%或100%。

## 6. Challenge History Details

点击曲目卡片的 Details 后，在当前卡片下方展开历史详情。

不要跳转新页面。

同一时刻最多展开一个 Segment 的 Details，以避免页面堆叠过长。

默认显示最近一页。

### 6.1 历史分页

每页最多10条挑战记录。

历史列表按照挑战时间倒序排列。

使用稳定的次级排序键，例如 attempt_id，避免相同时间戳导致不稳定排序。

历史 Attempt 编号应在该 Segment 的完整历史范围内连续且稳定，不要每页重新从1开始。

例如某 Segment 有26次挑战：

```text
Page 1: Attempt #26 至 #17
Page 2: Attempt #16 至 #7
Page 3: Attempt #6 至 #1
```

翻页按钮使用：

```text
[ Newer ]     Page 1 of 3     [ Older ]
```

第一页禁用 Newer。

最后一页禁用 Older。

分页控制同时放在详情区域顶部和底部。

顶部方便切换折线图，底部方便浏览完历史记录后翻页。

如果历史记录规模较大，采用服务端分页或可靠的游标机制，不要假设 Supabase 返回的数据没有默认行数上限。

### 6.2 Progress Chart

在每页 Challenge History 上方展示简洁的双折线图：

- Completion。
- Accuracy / Partial Accuracy。

Completion 使用暖金色实线。

Accuracy 使用绿色虚线，以降低和 Completion 的视觉混淆。

Y-axis 固定为0–100%。

可显示90%的成功阈值参考虚线，但明确它仅用于 Accuracy 成功判断。

X-axis 使用当前页实际 Attempt 编号。

折线图按时间正序，从旧到新展示。

历史卡片按时间倒序，从新到旧展示。

例如：

```text
Page 1:
Chart: #17 → #26
List:  #26 → #17

Page 2:
Chart: #7 → #16
List:  #16 → #7
```

折线图和历史列表必须来自同一个数据切片。

禁止在翻页后仍绘制全部历史数据。

首次加载、翻页和刷新时，必须保持图表和列表一致。

如果 Accuracy 缺失，应显示缺失点或断线，而不是将其作为0%。

未完成挑战可以展示 Partial Accuracy，并明确其不能与完整挑战 Accuracy 简单等同。

单条记录应显示数据点。空历史显示空状态。

优先使用简单 SVG 或工程已有图表能力，不要为了一个折线图引入大型第三方依赖。

### 6.3 历史记录卡片

每一次挑战显示：

- Attempt 编号。
- 挑战日期和时间。
- 本次挑战状态。
- Completion 进度条。
- Accuracy 或 Partial Accuracy。
- Personal Best 标记（适用时）。

示意：

```text
Challenge History                    26 attempts

Page 1 of 3                     Newer     Older

           Completion / Accuracy Chart

┌─────────────────────────────────────────────┐
│ Attempt #26                    Succeeded    │
│ Nov 15, 10:24                               │
│                                             │
│ Completion               Accuracy           │
│ █████████████ 100%          96.2%            │
│                                             │
│ 🏆 Personal Best                            │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│ Attempt #25                    Completed    │
│ Nov 14, 20:16                               │
│                                             │
│ Completion               Accuracy           │
│ █████████████ 100%          88.5%            │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│ Attempt #24                   Incomplete    │
│ Nov 14, 19:40                               │
│                                             │
│ Completion               Partial Accuracy   │
│ █████████░░░░ 72%           93.6%            │
└─────────────────────────────────────────────┘

[ Newer ]           Page 1 of 3       [ Older ]
```

单次 Attempt 状态使用：

- Incomplete
- Completed
- Succeeded

Segment 聚合状态则使用 In Progress、Completed、Succeeded。

Personal Best 必须根据完整历史计算，而不是当前10条记录的局部最高 Accuracy。

不要在该区域展示 MIDI 上传状态、Alignment 状态、Worker 状态或其他科研内部信息。

## 7. Product Layer 与 Research Layer 解耦

这是本次功能改造的核心工程要求。

检查现有：

- `src/pages/challenge/page.tsx`
- `src/features/challenge-history/api.ts`
- `src/features/challenge-history/gcs.ts`
- `src/features/challenge-history/upload-queue.ts`
- `src/features/challenge-history/types.ts`
- `backend/pianokt_backend/api.py`
- `backend/pianokt_backend/upload_service.py`
- `backend/pianokt_backend/projections.py`
- `backend/pianokt_backend/worker.py`
- 相关 Supabase migrations。

当前产品历史依赖 challenge_recordings 和 score_source = aligned 的设计，需要解除这种依赖。

目标架构：

```text
Challenge Terminal Event
          |
          +------------------------------+
          |                              |
     Product Result                 Research Bundle
          |                              |
  Local Durable Storage           Local Upload Queue
          |                              |
 Immediate UI Update             GCS Upload
          |                              |
 Lightweight JSON Sync           Finalize / Validation
          |                              |
 Product Result Store            Alignment / KC
          |                              |
 History / Leaderboard           KT / Research Dataset
```

两条链路使用相同的 attempt_id 或当前工程已经存在的稳定 Session Identity 建立关联。

不得为了产品统计重复创建新的科研 Attempt Identity。

### 7.1 产品成绩数据

建立独立于科研录音生命周期的产品成绩存储。

可以新增 `challenge_results` 表，或在充分检查现有 Schema 后，选择能真正满足独立持久化要求的等价方案。

首先评估是否能够复用已有数据契约，避免不必要的重复数据表。

不得继续使用必须等到录音 validated_at 或 READY 才产生的 projection 作为产品成绩唯一数据来源。

产品成绩应包含：

- attempt_id。
- user_id。
- source_environment。
- song_source。
- song_id。
- segment_id。
- song_version 或等价版本身份。
- midi_sha256（适用时）。
- frontend_note_accuracy。
- completion_pct。
- is_completed。
- is_succeeded 或等价派生状态。
- completion_reason。
- started_at。
- ended_at。
- duration。
- scoring_version。
- 可审计的曲目难度快照或稳定目录关联。

明确统一 Accuracy 和 Completion 的数值单位。建议内部使用0–1，UI 转换为0–100%。

不要混用比例和百分比。

产品成绩只在真实 Challenge 开始后结束时生成。

Countdown 取消、预览、未开始的会话不得产生挑战记录。

### 7.2 本地优先

在 Challenge 正常结束或有效提前退出时：

1. 在 Player stop/reset 清空状态前冻结成绩和 Challenge 上下文。
2. 本地持久化轻量 Product Result。
3. 立即更新产品 UI 状态和 SWR Cache。
4. 独立异步提交轻量 JSON 成绩。
5. 独立处理科研录音 Bundle。

产品结果持久化不得以 MIDI bytes 存在为前提。

即使 MIDI 序列化失败、录音为空、Telemetry 构建失败或 IndexedDB 中科研 Bundle 保存失败，仍应尽可能独立保存有效 Product Result。

不要使产品成绩必须和大型 MIDI Bundle 共同提交成功。

尽可能复用现有可靠的 IndexedDB 恢复机制，但产品成绩和科研录音应拥有独立的确认、重试和删除生命周期。

避免两个队列之间出现阻塞关系。

### 7.3 即时更新

Challenge 完成后，下列功能应立即反映本地已保存的成绩，无须等待科研处理：

- Continue Practicing。
- Challenged Songs。
- Best Completion。
- Best Accuracy。
- Succeeded 状态。
- Attempts。
- Last Played。
- Not Tried / Challenged 筛选。
- 当前用户本地可计算的 Leaderboard 统计。

合并服务器结果和本地未同步结果时，以稳定的 attempt_id 去重。

不能出现先显示成绩，随后服务器刷新导致成绩消失，再等待若干分钟重新出现的情况。

服务器确认产品成绩提交后才能清除对应的本地待同步记录，并确保数据交接不会出现瞬间缺失。

### 7.4 网络异常

需要明确支持：

- MIDI 上传失败但产品成绩提交成功。
- 产品成绩提交失败但科研录音上传成功。
- 两者均失败。
- 完全离线。
- 用户刷新或关闭页面。
- 请求响应丢失。
- 重复提交。
- 账户切换。
- Signed URL 过期。
- 后端 Worker 永久失败。

录音上传失败不应撤销已经获得的产品成绩。

科研上传恢复继续沿用现有机制。

产品成绩拥有独立轻量级重试机制。

只有产品成绩本身无法持久化或无法可靠同步时，才需要适当提示用户。

不要把科研上传错误作为用户 Challenge Result 的失败状态。

如果用户主动请求录音回放或下载，但录音不可用，应如实提示不可用。

### 7.5 安全与幂等

服务器必须验证：

- 用户身份和记录所有权。
- source_environment。
- Attempt Identity。
- 官方曲目及 Segment 身份。
- Accuracy 和 Completion 的合法范围。
- 完成状态与 terminal event 的一致性。
- 合法的曲目难度来源。

不要允许客户端直接提交任意 Leaderboard 加分或任意最高难度。

难度由服务端可信曲库或有效版本快照确定。

Succeeded 可由服务端使用经过范围检查的前端指标及完成状态计算。

重复提交同一 Attempt 不得重复计数。

同一个 Attempt ID 的冲突内容必须被明确拒绝或按照严谨的幂等契约处理，不允许静默覆盖历史。

采用正确的 RLS、权限和账户隔离措施。

明确记录：产品成绩依据客户端测量，因此不是完全防作弊的权威考试评分。这是当前以用户参与感为目标的产品设计取舍。

不要将科研录音验证重新引入产品成绩的生效条件。

## 8. Leaderboard

Leaderboard 必须保留在原有 PianoKT 主页的小模块中。

不重新设计整个模块，不增加独立排行榜页面，不改变其原有位置和整体风格。

只修改指标、查询 Data Source 和必要的排序逻辑。

移除原来的 Average Accuracy 排名。

新指标为：

1. Full Challenges。
2. Succeeded Songs。
3. Highest Difficulty Succeeded。

### 8.1 Full Challenges

统计用户完整完成的不同 Attempt 数量。

完整演奏结束，即使 Accuracy 未达到90%，也计入 Full Challenges。

提前退出不计入。

同一曲目完整挑战10次，则计10次。

不要求 MIDI 上传成功或 Alignment 完成。

### 8.2 Succeeded Songs

统计用户成功挑战过的不同 Segment 数量。

条件：

```text
is_completed = true
AND frontend_note_accuracy >= success_threshold
```

以稳定的 Segment Identity 去重。

同一个 Segment 成功挑战10次，只增加1个 Succeeded Song。

由于当前 PianoKT 的练习、推荐和收藏以 Segment 为单位，这里的 Songs 实际表达可挑战的 Segment，而非整首作品的数量。

不要混淆 song_id、segment_id、song_version 和 display title。

### 8.3 Highest Difficulty Succeeded

统计用户所有成功挑战过的合法曲目中的最高难度。

不能统计完整完成但未成功的曲目。

不能统计提前退出的曲目。

不能直接信任客户端提供的难度值。

如果曲库难度的定义不是统一的1–5级，不要为了 UI 强行建立未经验证的映射。

### 8.4 排行榜数据来源

排行榜改为从独立 Product Result 数据来源计算。

不再要求：

```text
score_source = aligned
validated_at IS NOT NULL
status = READY
```

继续保留 production 环境隔离。

完全离线时，本人的本地历史和成就可以立即更新，但公共排行榜只有在轻量成绩同步到服务器后才能更新。

不得把本地尚未同步的记录伪装成服务器已经确认的公共排名。

## 9. 历史数据兼容

检查旧版 challenge_recordings、user_play_logs、piano_attempts 和相关历史字段。

制定安全、可重复执行的历史迁移方案。

对于旧记录：

- 优先使用可信的 frontend_note_accuracy。
- 如果确认旧版 client_accuracy_pct 属于相同前端 Note-level Accuracy 语义，可以进行明确的单位转换。
- 不得用 backend_event_accuracy 或旧版语义不明确的 accuracy_pct 冒充 frontend_note_accuracy。
- 不得无依据推断历史 Completion。
- 不得把未知是否完整完成的记录直接标记为 Completed。
- 不得通过曲目标题猜测 Segment Identity。
- 无法确定的字段保留 unknown/null。
- 无法安全关联的旧记录不得错误关联到新曲目。

历史迁移必须幂等，不得重复增加排行榜次数。

历史记录缺失前端正确率时，UI 显示 `—`。

不要删除已有科研数据或修改旧的 immutable alignment artifacts。

另外核查 migration `021_explicit_accuracy_metrics.sql` 与 `projections.py` 对 user_play_logs 的字段要求是否一致。此前代码检查发现可能存在 Schema 不匹配，需要根据当前实际代码确认并修正，但不得盲目执行生产迁移。

## 10. 科研层保护

以下内容不属于本次改造范围：

- Alignment 算法。
- Reference Grouping。
- Performance Grouping。
- KC Labeler。
- KT 模型。
- AKT / FM-KC 推理。
- Monte Carlo Rollout。
- 推荐策略的数学定义。
- 原始科研数据格式。
- 已有科研数据集中的响应标签。
- 训练和实验逻辑。

继续严格区分：

```text
frontend_note_accuracy
    → Product feedback / Challenge result

backend_event_accuracy
    → Alignment / KC response / Research analysis
```

产品层成功不代表科研数据有效。

科研训练、Knowledge Tracing 更新与数据集导出，仍然必须遵循原有 READY、数据质量、版本和 provenance 检查。

禁止使用只有 Product Result 但没有有效后端响应标签的记录作为 KT 训练数据。

不要将产品正确率冒充后端 Event-level Accuracy。

## 11. 实施阶段

按以下阶段执行，避免一次性大规模修改造成不可控问题。

### Phase 1：代码审计与视觉实现

- 检查现有代码、路由、组件、样式和测试。
- 实现 All Songs / Challenged Songs 内部 Tab。
- 实现 Continue Practicing。
- 实现 Recommended for You。
- 复用原有 SegmentTable。
- 增加 Favorites、Not Tried、Challenged 筛选。
- 实现 Challenged Songs 卡片。
- 实现 Details 历史分页。
- 实现当前页折线图。
- 保留主页及其他页面不变。
- 使用明确隔离的开发测试数据完成视觉验收。

不得将模拟数据接入正式产品路径。

### Phase 2：产品成绩数据链路

- 实现独立 Product Result 契约。
- 实现本地优先持久化。
- 实现独立成绩提交与重试。
- 实现本地和服务器结果合并。
- 接入真实 Continue Practicing 和 Challenged Songs。
- 实现可靠的历史分页与聚合。
- 执行必要的开发环境 Schema 迁移及兼容性测试。

### Phase 3：Leaderboard 和历史兼容

- 保留原有主页模块布局。
- 更新三个排行榜指标及排序。
- 实现安全的历史数据兼容方案。
- 验证用户隔离、生产环境过滤和幂等性。

### Phase 4：完整验收

- 检查桌面端和移动端 UI。
- 测试真实 Challenge 生命周期。
- 测试不同上传失败场景。
- 检查旧功能及科研 Pipeline 未出现回归。
- 提供最终修改说明。

每个阶段完成后运行对应测试并记录结果。可以在本地完成全部阶段，但不得未经明确授权部署生产环境、修改生产数据库或推送生产发布。

## 12. 必须通过的测试

### UI 测试

- 默认进入 All Songs。
- 两个 Tab 正常切换。
- Continue Practicing 显示正确的最近挑战。
- 新用户没有空的 Continue Practicing 卡片。
- Favorites 状态在所有区域同步。
- 搜索先于分页。
- 筛选先于分页。
- All Songs 每页最多10首。
- 原有试听按钮是独立三角形。
- 试听和收藏不会触发 Challenge Preview。
- 不出现新的 Challenge Selection 面板。
- 原有 Challenge Preview、MIDI 设置和播放器正常工作。
- Practice a song 页面没有被意外修改。
- 主页和 Leaderboard 原布局保持不变。

### Challenge History 测试

至少构造以下历史规模：

```text
0、1、9、10、11、20、26、100、1001 条
```

验证：

- 每页最多10条。
- 当前页图表和历史记录对应。
- 最新页和最旧页顺序正确。
- 翻页不改变全量 Best Accuracy。
- 翻页不改变全量 Best Completion。
- 翻页不改变 Personal Best。
- 翻页不改变 Succeeded 状态。
- 只有一个曲目 Details 展开。
- 大规模历史不会被 Supabase 默认行数限制截断。
- 数据刷新或新增 Attempt 后分页保持有效。

### 产品成绩测试

- 完整挑战达到90%，判定 Succeeded。
- 完整挑战低于90%，判定 Completed。
- 提前退出但 Accuracy 为100%，不得判定 Succeeded。
- 尚未完整挑战，Best Accuracy 显示 `—`。
- 已 Succeeded 后再次失败，历史状态不下降。
- Countdown 取消不生成挑战记录。
- MIDI 文件生成失败但 Product Result 仍然能够保存。
- GCS 上传失败不影响产品成绩。
- Alignment 失败不影响产品成绩。
- 完全离线时个人历史仍然更新。
- 页面刷新后未同步成绩仍存在。
- 重复提交不会重复计数。
- 服务器确认前后合并不会发生历史闪烁。
- 两个账户之间不存在成绩和待上传数据泄漏。
- 生产、开发和测试环境统计隔离。

### Leaderboard 测试

- 同一 Segment 完整挑战10次，Full Challenges 增加10。
- 同一 Segment 成功挑战多次，Succeeded Songs 只增加1。
- 提前退出不增加 Full Challenges。
- 失败挑战不增加 Succeeded Songs。
- 未成功的高难度曲目不增加 Highest Difficulty Succeeded。
- GCS 上传失败但 Product Result 同步成功，排行榜指标正常更新。
- Worker 失败不撤销已经确认的产品成绩。
- 重复请求不增加排行榜次数。
- 历史迁移重复执行不会重复计数。
- 非法客户端难度值不能提高排行榜成绩。

## 13. 工程验证

根据实际仓库可用命令运行：

```bash
npm run check-types
npm run build
npm run test:discovery
npm run test:catalog
npm run test:activity
npm run test:uploads
npm run test:protocol
```

同时运行现有 Challenge MIDI 安全测试、暂停测试、空格快捷键测试及相关后端 pytest。

为新增功能补充自动化测试。

不得通过删除旧测试、放宽安全断言、跳过错误或 mock 掉真正需要验证的逻辑来获得通过结果。

必须使用实际浏览器检查：

- Desktop layout。
- Mobile layout。
- All Songs 表格。
- Continue Practicing。
- Recommended for You。
- Challenged Songs。
- 展开的 Challenge History。
- History Page 1、Page 2 和最后一页。
- 筛选和收藏交互。
- 真实 Challenge 返回页面后的即时成绩更新。

视觉验收应以现有 PianoKT 页面为基础，而不是使用一个重新搭建的通用 Dashboard。

## 14. 最终交付

完成后提供：

1. 实现总结。
2. 修改文件清单。
3. Product Result 数据契约。
4. 数据库 migrations 及其应用前置条件。
5. 历史兼容和迁移方案。
6. 本地运行方式。
7. 全部测试结果。
8. 桌面端和移动端截图。
9. 未完成事项、实际限制及潜在风险。
10. 确认没有未经授权的生产部署。

最重要的原则：

**不要重新设计 PianoKT 网站本身。保留原来的 PianoKT，只替换 Take a challenge 页面中间的内容布局和产品成绩逻辑。**

**用户练习成绩以前端 Note-level Accuracy 和真实 Challenge Completion 为准。科研录音、Alignment 和 KC 数据处理独立异步运行，不得影响用户已经取得的练习成就。**