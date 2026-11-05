# 云端录音保存：第一轮性能优化（阶段记录）

当前已升级为 [异步 finalize v3 / migration 017](ASYNC_FINALIZATION_ZH.md)。以下记录第一轮优化：签名/认证/数据库计时仍有效；GCS 验证已移出 API，不能再用旧同步流程理解当前行为。

更新：2026-11-05。针对结束弹奏后录音迟迟不出现在云端的链路。本轮没有数据库迁移，没有修改 GCS 对象结构或 v2 协议，也没有执行云端部署。

## 当前流程

浏览器封存完整 Bundle 到 IndexedDB → 后台创建 `/attempts` → 最多三份文件并行 PUT → 全部成功或返回 412 → finalize 验证 → 原子提交事件、投影与 outbox。

本地保存、云端保存、Worker 评分完成仍是三个不同状态。页面离开仍只等待本地封存；云端列表在 finalize 成功后才有记录，评分随后由 Worker 完成。

## 已实施

1. API 进程复用 HTTPX Client，保留每次请求的 Supabase 用户验证和白名单 RPC，不缓存用户权限。Authorization 仍按请求传入。
2. API 复用 GCS Client 和签名 Credentials。签名 Token 仅在非 FRESH 状态刷新；刷新加锁，防止并发重复刷新。每个签名 URL 仍需要 IAM signBlob，签名暂未并行。
3. API lifespan 开启 PostgreSQL 连接池，默认每进程最多 4 个连接，最小为 0，获取等待上限 10 秒。连接归还前提交或回滚；保留 extra_float_digits=1，关闭自动 prepared statements，以兼容事务池。Worker/CLI 保留原专用连接方式及 session lock 语义。
4. 浏览器两份 MIDI 哈希并行计算；演奏 MIDI、参考 MIDI、按键侧车最多三个 PUT 并行。等待所有 PUT 完成后再判断失败，失败不 finalize，完整 Bundle 保留。412 仍须经服务端摘要校验。
5. 已经提交成功的 finalize，在 owner、环境和完整 finalization 一致时直接返回，不再次下载 GCS；不同内容仍返回 409。首次提交仍检查大小、generation、SHA-256、MIDI/侧车格式，再进入数据库事务。
6. 上传运行期间的新唤醒不再被丢弃：当前队列快照处理完会立即再取一轮，并使用最近请求恢复的 owner。录音之间仍串行，避免无界并发；每次 API/PUT fetch 配置 120 秒 AbortSignal 超时。失败包继续保留，在线事件及 30 秒定时器继续负责恢复。页面休眠时浏览器超时行为不等价于严格墙钟截止时间。
7. 增加请求和阶段计时，便于定位下一轮优化。

连接资源在 API 正常关闭时释放。不要关闭 ASGI lifespan，否则 API 不会启用连接池。环境变量变更后需要重启进程。

## 日志怎么读

后端通过 uvicorn.error 的 INFO 日志输出 `request_id`、`stage`、`duration_ms`、`outcome`，响应头返回 `X-Request-ID`，CORS 允许前端读取它。

- `auth.user` / `auth.allowlist`：两个认证相关远程调用。
- `db.acquire`：等连接（包括首次建连）的时间。
- `db.transaction`：SQL、锁等待及提交的总时间，不是单条 SQL 时间。
- `db.attempt_lock`：获取 attempt advisory lock 的时间；嵌套在事务时间内，不能与事务时间简单相加。
- `gcs.credentials_refresh`：只有需要刷新签名凭据时出现。
- `gcs.sign`：单份 URL 签名。
- `gcs.metadata.*` / `gcs.download.*`：各文件的 GCS 元信息与下载。
- `finalize.digest.*` / `finalize.midi_validate` / `finalize.telemetry_validate`：内容校验。
- `http.*`：包含认证在内的接口处理耗时；业务 4xx 返回也可能是正常完成 middleware，需结合 HTTP access log 状态码判断。

浏览器控制台开启 Debug/Verbose，查看 `[Upload timing]` 中 `/attempts`、`put.<attempt_id>.<index>`、`/finalize`；`[Upload request]` 可关联后端 request_id。前端 fetch 计时截止响应头到达，不包含随后 JSON 解析。日志不输出 Token、签名 URL 或录音正文。

本轮没有逐条 SQL tracing、完整本地打包/IndexedDB 计时或生产 p50/p95 仪表盘。先比较同一环境下冷请求与连续暖请求；不要把首次 ADC 初始化、服务冷启动和正常请求混为一谈。

## 启用与验证

```bash
.venv/bin/pip install -r backend/requirements.lock
# 可选；默认已经是 4。多实例总连接数需按实例数 × 进程数 × 此值计算。
# DB_POOL_MAX_SIZE=4
```

重启后端并刷新新前端。本轮不需要新 SQL migration（现有 v2 仍要求已应用 016）。SIGNING_SERVICE_ACCOUNT 和现有 IAM 权限保持不变，不需要生成服务账号私钥。开发验证仍使用 PIANOKT_ENV=development。

录一段，检查三份 PUT 时间重叠、finalize 位于最后一份 PUT 之后，云端列表出现记录。再测试断网重试、某份上传失败、切换账号，以及第一段上传期间保存第二段。首次 finalize 的校验仍在，不能用失败包验证出成功记录。

自动化验证覆盖：并发 Token 刷新去重/过期刷新、连接池失败回滚后归还、精度与事务池选项、API 启停、三 PUT 并行、失败等待其他 PUT 且不 finalize、新任务唤醒及 owner 切换、已提交 finalize 无 GCS 访问的重试，以及既有 v2 完整性测试。

## 下一轮依据计时决定

签名仍占主要时间时考虑有限并行 signBlob；下载验证占主要时间时考虑三文件有限并行验证；SQL 占主要时间时再批量改写事件插入与冲突核对。JWT 本地验证及白名单缓存需要单独定义权限撤销语义，本轮没有修改。

没有采集真实云端前后对照数据，因此目前只能确认移除了重复刷新、重复建连和串行 PUT，不能声称已经把生产耗时降低到某个秒数。此优化也不改变 outbox relay 调度或 Worker 评分耗时。

## 本次验证结果

2026-11-05：后端 74 passed / 4 skipped（3 项独立 PostgreSQL 并发测试、1 项 Spark 测试）；前端类型检查、生产构建、上传恢复测试、协议/并行失败/队列唤醒测试及 git diff --check 通过。新增连接池测试使用模拟连接验证配置和归还行为，未对真实 Supabase 做连接池负载测试。
