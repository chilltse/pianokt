# 曲目与片段管理

## 最终身份模型

- `song_id`：整首逻辑曲目/编曲的 UUID。同一首长曲的所有片段共用。
- `segment_id`：可独立演奏片段的 UUID。网站选择、路由、设置及成绩按片段区分。
- `segment_index`：同一曲目内从 1 开始的唯一正整数，不是业务主键，不自动重排。
- `midi_sha256`：原始 MIDI 文件字节哈希，用于不可变内容、缓存和研究追溯。

不使用 `song_version_id`。即使不分段，整首曲目也是一个 segment（默认 index=1）。CLI 接收已经切好的 MIDI，不自动切割。

数据库三张表：`piano_song_catalog` 保存曲目，`piano_song_segments` 保存片段及 `active_midi_sha256`，`piano_song_midis` 用 `(segment_id,midi_sha256)` 保存不可变内容元数据。相同内容跨片段只保存一份 Storage 文件。替换片段 MIDI 时保持 song/segment UUID，新增内容哈希并切换 active SHA。

前端一次调用 `piano_public_catalog()` RPC（聚合 `piano_public_song_catalog`，避免默认行数上限截断目录），每个已发布片段一行。只暴露 published song + published segment + 当前内容；客户端没有底层表读写权限。客户端播放 SWR key 包含 segment ID 和 SHA，IndexedDB 用 `midi:<sha256>` 保存原始 ArrayBuffer。后台目录更新不清空其他 MIDI 缓存。

Challenge 的 `reference_hash` 仍标识根据本次范围、手别和转调生成的参考快照，Alignment `score_version` 仍为该快照哈希。它们与原始 `midi_sha256` 含义不同。上传、worker 结果、Bronze/Silver/Gold、Attempt 导出和训练导出携带 song、segment、index、原始 SHA；Exercise identity 包含片段、SHA 和影响练习内容的设置。推荐和片段成绩也按 segment 区分。

## 一次性安装

1. 使用 Python 3.13，在仓库根目录安装 CLI：

   ```sh
   python3.13 -m venv .venv
   . .venv/bin/activate
   pip install -e ./backend
   pianokt-data import-song --help
   ```

2. 在 Supabase 执行最终的 `supabase/migrations/018_song_versions.sql`。文件名保留编号，内容已改为以上无版本 ID 的最终设计。它会清空并重建旧曲库三层结构，不保留旧文件名映射；不会删除练习记录和 GCS 文件。若曾执行上一轮旧版 018，直接执行现在的 018 即可替换；若最终 `piano_song_midis` 已存在，脚本会在任何删除之前拒绝重复执行，防止误清空新曲库。由 migration 工具管理的环境应确认这一修订是否已标记为执行，必要时在管理员 SQL editor 应用本文件，不能仅依赖 migration 状态。
   **已有新版曲库：只执行 `019_practice_catalog_identity.sql`，不要重跑 018。** 新环境执行 018 后再执行 019。019 为 activity、upload、录音和练习汇总增加类型化 segment/index/SHA 字段，并创建目录 RPC；可重复执行，不清空曲库或练习数据。先部署 019，再发布 API、Worker 和前端。
3. Bucket `piano-midi`：public、10 MiB 上限、允许 `audio/midi` / `audio/x-midi`；没有 anon/authenticated 上传或覆盖策略。新建由 migration 处理，已存在 bucket 不覆盖，需核对配置。未来私人 MIDI 使用另一个 private bucket。
4. 仅在管理员终端设置同一个 Supabase 项目的三个环境变量：

   ```sh
   export SUPABASE_URL='https://你的项目.supabase.co'
   export SUPABASE_SERVICE_ROLE_KEY='服务端管理员密钥'
   export DATABASE_URL='具有曲库管理权限的PostgreSQL连接串'
   ```

   数据库连接应为管理员连接，普通 `pianokt_backend` 角色只有读取曲库的权限。凭据不要写入 git 或任何 `VITE_*` 变量。前端继续用已有 `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY`。
5. 现有专用后端角色由 018 补充新表授权；新环境使用更新后的 `supabase/backend_role.sql`。先部署 SQL 和后端，再部署前端。本次未对远端运行 SQL 或上传。

## 上传完整曲目

先填写实际核验的许可证和真实来源链接。CLI 要求显式填写，不会自动判断作品/编曲的版权，也不会默认使用示例许可。来源参数使用纯 URL，不要使用 Markdown 链接。

```sh
export VERIFIED_LICENSE='实际核验的许可名称或许可链接'
export VERIFIED_SOURCE_URL='https://实际来源页面'

pianokt-data import-song ./Chopin-Nocturne-Op9-No2.mid \
  --title 'Nocturne Op. 9 No. 2' \
  --composer 'Frédéric Chopin' \
  --license "$VERIFIED_LICENSE" \
  --source-url "$VERIFIED_SOURCE_URL" \
  --slug chopin-nocturne-op9-no2-arrangement-a \
  --dry-run
```

确认元数据后，去掉 `--dry-run` 再执行，自动上传并发布。`--dry-run` 不需要凭据，不访问网络、不改数据库。成功输出 JSON 含 `song_id`、`segment_id`、`segment_index`、`midi_sha256`、`status=published`，保存这些标识即可。

`--slug` 是管理员维护的唯一编曲标识；省略时由 composer/title 生成。同名曲目的不同编曲必须使用不同 slug。重复同一命令复用同一 song、segment 及 SHA，不创建重复内容。默认难度 0.5 只是未校准占位值，可用 `--difficulty 0.35`；分类可重复传 `--genre classical`。

## 上传多个片段

第一个片段同上，增加：

```sh
--segment-index 1 --segment-title 'Measures 1–16'
```

第二个片段保留相同曲名、作曲家、版权与来源，指定第一次返回的 song UUID：

```sh
pianokt-data import-song ./nocturne-part-02.mid \
  --song-id '第一次输出的song UUID' \
  --segment-index 2 \
  --segment-title 'Measures 17–32' \
  --title 'Nocturne Op. 9 No. 2' \
  --composer 'Frédéric Chopin' \
  --license "$VERIFIED_LICENSE" \
  --source-url "$VERIFIED_SOURCE_URL"
```

多个片段共享 song UUID，分别有自己的 segment UUID、index、SHA。目录显示曲名和片段名。新导航中的 `id` 为 segment UUID。

更新某个片段时，用相同 `--song-id` 和 `--segment-index` 上传新文件。新 SHA 保存为不可变对象，旧 SHA 不覆盖，新目录指向新 SHA。重复当前 SHA 是幂等操作；重放已被替换的旧 SHA 会报错，防止旧脚本意外回退。导入不会重命名已有曲目/片段，也不会静默修改已有难度和分类；元数据编辑可以通过管理员数据库操作完成，版权来源按内容保留。

## 校验、失败与发布

校验 MThd、10 MiB 上限、type 0/1、PPQ、最多 256 tracks、50 万 notes、四小时时长；拒绝空曲、无法解析及缺少 note-off 的 MIDI。提取音符数、轨道数、时长、调号变化、tempo map、拍号变化。未知调号保留为空，初始无 tempo 事件时按 MIDI 默认 120 BPM 记录。零时长 note / 多余 note-off 在 metadata.validation_warnings 中报告，原始字节不修改；全为零时长音符则拒绝。

Storage 使用 `midi/<sha256>.mid`，禁止 upsert，设置一年 immutable Cache-Control。读取已有对象或上传后，校验管理员下载和匿名 CDN 下载的 SHA。验证通过后才在数据库事务内创建/激活片段并发布。并发导入使用事务锁保护 song/segment 身份。存储失败不会发布目录；数据库失败可能留下未引用的内容对象，重跑同一命令复用它，不自动删除共享文件。不同 SHA 的并发更新按数据库事务提交顺序生效。

发布完成，网站进入/刷新曲库会获取新目录；首次打开片段按需下载，后续从 IndexedDB 读取，SHA 更新只下载新内容。预下载可选；Challenge 的 GCS 流程不变。没有应用 Shell Service Worker，不能把 MIDI 缓存等同于完整应用断网冷启动。

## 示例曲目与测试

前端 `src/manifest.json`、静态 MIDI 及生成逻辑已移除。17 首旧示例 MIDI 存档在 `scripts/song_catalog/midi/`，元数据在 `scripts/song_catalog/legacy-manifest.json`，仅供管理员和解析测试使用，不会被 Vite 作为静态资源发布。

如要重新导入这些示例（并非新曲库必需），先自行复核许可，再运行 `.venv/bin/python scripts/import_song_catalog.py` 本地检查，增加 `--apply` 上传。它调用同一个 importer，不维护旧 ID 映射；原元数据缺少作者的条目标记 Unknown，原版权和来源 URL 原样保留。

验证命令：

```sh
npm run test:catalog
npm run test:protocol
npm run test:activity
npm run test:uploads
.venv/bin/python -m pytest backend/tests -q
npm run check-types
npm run build
```

`npm test` 仍需 Bun。单元测试覆盖 MIDI 校验、CLI dry-run、Storage 去重和 SHA 验证、事务发布/重复执行/片段隔离/更新及回退保护。数据库测试适配器与浏览器缓存适配器不代替真实 Supabase/RLS/IndexedDB 验收；部署后用管理员导入一首实际许可确认的曲目，再匿名访问目录/CDN，并在浏览器刷新检查同一 MIDI 没有重复下载。


## 全链路检查补丁（019）

首页预览改从实时目录选择片段，不再引用文件名。`SongMetadata.id` 和路由 `id` 表示可演奏条目：官方为 segment UUID；本地仍为本地文件身份，绝不能把整首 song UUID 传给官方播放器。

Activity 从首次 play_started 开始携带完整 song/segment/index/SHA，含中断、重载和离线重放；数据库 student_attempts 独立保存它们，不依赖录音最终上传成功。API 统一验证 UUID、字段完整性、曲目来源及 catalog 归属，Finalize 拒绝切换片段或原始 SHA。在线 projection 和 KT 导出共用 Exercise 身份算法。练习中的 MIDI 固定为开始时的内容，后台刷新不会中途换谱。

旧迁移 000–017 中的旧字段属于历史 schema 演进；016 已撤销旧写入 RPC，它们不作为新版业务入口。不删除 Alignment 的 score_version、reference_hash、performance_hash，也不删除用于数据集发布协议的 manifest.json：它们不是被移除的前端静态曲库清单。

019 不猜测历史片段。新版验证器拒绝携带旧 song_version_id 或缺少 segment/SHA 的官方活动包；重新录制使用新协议。目录缓存会忽略缺少新身份的旧条目，原始 MIDI 的 SHA 缓存仍可复用。

数据库回归可用隔离 PGlite 运行：设置 `PGLITE_MODULE` 指向已安装的 `@electric-sql/pglite/dist/index.js`，再运行 `.venv/bin/python -m pytest backend/tests -q`。它执行实际 018/019 SQL、匿名权限、发布、Activity、上传和 projections；不会连接真实 Supabase。真实 PostgreSQL 并发测试另需专用 `PIANOKT_TEST_POSTGRES_DSN`，不能填写生产连接。


## 412 与旧待上传包的 Finalize 422

GCS PUT 使用 `x-goog-if-generation-match: 0`，同一路径已有文件会返回 412；前端继续提交 Finalize，由 Worker 按声明的哈希验证已有内容。这是防覆盖设计，不应删除对象或取消前置条件来“修复”它。

上传注册 POST /attempts 保存曲目身份；三个 GCS 文件通过 attempt 路径关联。Finalize 只提交 attempt 引用和事件，不重复提交 song/segment/SHA。后端直接使用本次已读取且校验归属的 piano_attempts 记录解析曲目信息，不新增 HTTP 往返或目录查询。旧缺失字段的练习记录不自动补全；重新开始练习即可使用新协议。
