请协同检查并重构以下两个工程：

- `pianokt_300_auto`（/Users/tse/Downloads/pianokt_300_auto）
- `pianokt`（当前 `feature/ui_redesign` 分支）

本次目标是简化整个曲库管理流程，统一更新 Supabase 官方曲库，并真正接通 Entire Song Practice。

## 1. 统一曲库管理

将 `pianokt_300_auto` 作为唯一的本地曲库生产和管理入口。

将 PianoKT 中原始的17首内置曲目纳入该工程的统一曲库管理，来源为：

- `scripts/song_catalog/legacy-manifest.json`
- `scripts/song_catalog/midi/`

同时纳入现有：

- `output/accepted/`
- `output/intermediate_advanced/accepted/`
- 其他有效的已审核曲库

统一曲目身份、版权信息、元数据、去重和切分逻辑。

保留已有来源及难度分类，但不再维护不同的上传流程。

每首曲目统一包含：

- `reference.mid`：完整原始曲目
- `metadata.json`
- `segment_001.mid` 至 `segment_N.mid`

## 2. 统一切分规则

复用现有 `phrase_v5.py`，不要重新开发一套切分算法。

- 所有曲目始终保留完整原始 MIDI。
- 不超过50秒的曲目，可直接作为单个 Segment。
- 超过50秒的曲目，使用 phrase_v5 进行音乐结构感知切分。
- Segment 目标时长30秒，范围20–40秒。
- 保留切分边界、左右手映射及 segmentation version。
- 不允许强制截断持续音符、破坏和弦或丢弃曲目尾部。
- 无法合理切分或无法验证左右手身份的曲目，明确报告并进入人工审核，不伪造切分结果。
- 对已经符合规则的曲目不重复切分。

重点完成原始17首曲目的审计与重新切分。

## 3. 更新 Supabase 官方曲库

检查当前线上曲库与本地曲库的对应关系，制定实际更新计划。

要求：

- 所有曲目均拥有独立的完整 MIDI 资源。
- 每首曲目拥有对应的 Segment 资源。
- 保留现有 `song_id` 和未发生语义变化的 `segment_id`。
- 对切分边界发生变化的 Segment，进行正确的身份及版本迁移，避免旧成绩和收藏错误关联。
- 对原始17首中曾将长曲作为单个 Segment 发布的情况，更新为完整曲目和正确的分段集合，旧长 Segment 不再进入 Challenge 和推荐候选集。
- 保留原有历史数据，不直接删除历史引用的资源。
- 不允许重复曲目。
- 保留 SHA-256 内容寻址和不可变文件版本机制。
- 整曲和单个 Segment 内容相同时，复用同一个 Storage 文件。
- 新增必要的非破坏性数据库迁移及完整曲目资源查询机制。

不要使用 Segment 0 表示整曲，也不要通过拼接 Segments 重建整曲。

## 4. 简化上传和同步流程

整合现有 `batch_upload_songs.py`、`import_song_catalog.py` 和 `pianokt-data import-song` 的相关能力，提供一个统一的曲库同步入口。

建议接口：

```bash
pianokt-data sync-catalog \
  --catalog-dir ~/Downloads/pianokt_300_auto \
  --dry-run
```

正式更新：

```bash
pianokt-data sync-catalog \
  --catalog-dir ~/Downloads/pianokt_300_auto \
  --apply
```

同步工具应自动完成：

- 扫描本地有效曲库。
- 验证元数据与 MIDI。
- 检查跨目录重复曲目。
- 对比 Supabase 已发布曲库。
- 识别新增曲目。
- 补充缺失的完整 MIDI。
- 更新发生变化的 Segment 集合。
- 保留未变化的曲目及版本。
- 验证上传后的文件完整性。
- 输出统一同步报告。

支持幂等执行、失败重试、明确的环境选择及曲目级原子发布。

不再要求手动修改脚本中的绝对路径，也不要要求分别执行多套上传脚本。

旧导入工具如仍有兼容性用途，可以保留，但统一同步入口应作为推荐的管理员操作方式。

## 5. Practice 和 Challenge 页面

保留当前已经完成的 Song-level 折叠与分组逻辑。

Practice：

- 每首曲目只展示一次。
- 展开后第一行为 `Entire Song`，之后为 Part 1、Part 2 等。
- Entire Song 支持完整曲目试听和练习。
- Entire Song 不显示 Mark 按钮，也不提供 Mark 操作。
- Part 保留现有试听、Mark 和练习功能。
- 复用 MIDI loader、SHA-256 校验、缓存、试听播放器及练习播放器。
- 整曲 Practice 使用正确的 Song-level 身份记录。

Challenge：

- 每首曲目只展示一次。
- 展开后只展示 Part，不展示 Entire Song。
- 保留 Segment 试听、Mark、历史最佳成绩及挑战状态。
- 父行显示完成数量和总段落数量。

Continue Challenge 和两个页面的 Recommended for You 继续以 Segment 为单位，不改变推荐系统的候选粒度。

## 6. 实际更新与验收

首先执行只读审计和更新计划，明确：

- 原始17首曲目的当前状态。
- 本地及线上曲目数量。
- 现有 Segment 数量。
- 缺少完整 MIDI 的曲目。
- 待重新切分的曲目。
- 重复作品。
- 预计新增、更新和归档的资源。
- 对历史数据的影响。

随后完成代码、迁移及 development/test 环境下的实际同步验证。

验证通过后，提供明确的生产环境更新命令及更新前备份、回滚方法。未经明确确认不要直接修改生产环境。

最终验收：

- 所有可发布曲目均拥有完整 MIDI。
- 原始17首已按照统一规则完成处理，例外情况有明确报告。
- Practice 可以真正试听并练习 Entire Song。
- Entire Song 没有 Mark 功能。
- Challenge 只允许挑战有效 Segment。
- 不存在重复曲目或失效的活动 Segment。
- 重复执行同步不会产生重复数据。
- 现有历史记录、Alignment、KC Labeler、KT 和推荐系统不发生回归。

不要仅完成前端占位或提供设计方案。完成实际工程实现，并交付数据库迁移文件、统一同步命令、测试结果和真实阻塞项。

核心原则：一个曲库生产入口、一个同步入口、一个线上权威曲库，两种练习模式。