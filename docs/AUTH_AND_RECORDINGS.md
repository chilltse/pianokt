# 登录、权限与 Challenge 录音

更新日期：2026-11-03。本文对应当前 Supabase + FastAPI + 私有 GCS 实现；整体关系见 [架构总览](ARCHITECTURE_ZH.md)。数据库定义以 migrations 为准，不再在文档复制一套可能覆盖新函数的旧 SQL。

## 1. 三类权限各管什么

- **Supabase Auth**：确认学生是谁，提供登录 session 和 access token。
- **PostgreSQL RLS + 后端归属检查**：控制谁能读写在线业务记录。
- **GCP IAM + GCS 签名 URL**：控制后台进程与私有文件访问。

前端路由重定向只是交互保护；修改 URL 中的 userId 不应改变实际数据权限。录音查询依赖 RLS，API 每次按认证用户核对 attempt 归属。

## 2. 登录与白名单

`src/features/auth/supabase.ts` 用 VITE_SUPABASE_URL 与 VITE_SUPABASE_ANON_KEY 创建客户端。AuthProvider 支持邮箱注册/登录、Google OAuth、退出与 session 刷新。

认证成功后，前端执行 check_user_allowed；后端业务请求也独立验证 token 并执行这个 RPC。白名单表 allowed_emails 为空时，RPC 允许所有已认证用户；有内容时，只允许命中的邮箱。不能把“Auth 登录成功”与“通过应用访问策略”混为一谈。

本地开发可显式跳过白名单：前端为 DEV + VITE_PIANOKT_LOCAL_BYPASS_ACCESS_LIST=true，后端同时为 PIANOKT_ENV=development、PIANOKT_LOCAL_DEV_BYPASS=1 且没有 K_SERVICE。它仍要求真实 Supabase 身份。Cloud Run 不接受这个绕过。

Google provider 与邮箱确认跳转地址在 Supabase 项目配置；本地地址见 [本地开发](LOCAL_DEVELOPMENT_ZH.md)。不要把 OAuth secret、数据库连接串或 service-role key 放入 VITE_*。

## 3. 资料与头像

migration 003 负责 profiles 与 auth 资料同步；004 提供 upsert_profile_from_auth，前端在登录检查后调用。账号页和 AppBar 读取相关资料。

当前头像代码 `features/auth/uploadAvatar.ts` 使用 Supabase Storage 的公开 avatars 桶，路径为 `<user-id>/avatar.<ext>`，采用 upsert。客户端限制 2 MiB，接受 JPEG/PNG/WebP/GIF。写策略应限制为 auth.uid() 对应的第一层目录；公开桶只意味着头像可公开读取，不应开放其他用户的写权限。

avatars 是头像功能的存储，与新的私有 GCS Challenge 录音不同。头像上传及资料更新也不能被描述为横跨 Storage/PostgreSQL 的全局事务。

## 4. 新录音如何保存

当前 `saveChallengeRecording` 导出指向 `gcs.ts:saveGcsRecording`。

1. 原始 owner、attempt ID 和完整录音包先封存到 IndexedDB。
2. POST /attempts 创建上传任务，拿临时 GCS 地址。
3. 浏览器上传 performance.mid、reference.mid，以及新客户端的 performance-events.json。
4. POST /attempts/{id}/finalize 验证输入，在一个数据库事务内保存任务状态、录音列表、播放汇总和 outbox。
5. finalize 确认成功后，删除本地上传包；后台之后再执行对齐。

录音页面用 challenge_recordings 读取列表。新行的 midi_storage_path 是 `gcs:<attempt-id>`，下载 helper 通过 API 换取短时签名地址。

历史数据如果仍是 Supabase Storage 路径，下载 helper 走旧 challenge-recordings 桶，签名有效期为一小时。新 GCS URL 当前由 API 统一签为 10 分钟。历史读取兼容不意味着新录音仍上传 Supabase Storage。

## 5. 访问时间线与录音列表不是同一张表

visit 由全站 activity 模块维护；student_attempts 在实际演奏开始时建立；student_attempt_events 保留暂停、继续、退出与中断等节点。

challenge_recordings 只有在录音最终提交后出现。关闭页面但没封存上传的演奏可能有活动上下文，没有可下载 MIDI；反过来，旧录音也可能缺新 visit 上下文。

身份关系、日期与顺序规则见 [访问和数据集](VISITS_AND_ATTEMPT_DATASET_ZH.md)。

## 6. 正式分数与排行榜

新录音最初 score_source=pending，client_accuracy_pct 保存浏览器反馈。worker 质量通过且有有效 accuracy 时，将 accuracy_pct 更新为后端计算的百分比，并标记 aligned；无有效正式分数则 rejected。

get_leaderboard 的 accuracy_avg 仅统计 aligned。挑战次数仍统计已最终提交的录音，难度仍来自现有业务字段。没有正式正确率时返回 NULL，页面显示“—”。旧数据默认 legacy，不自动当成已经重新对齐的正式成绩。

首页按 challenge_recordings 和 profiles 变化订阅 Realtime，收到可见事件后重新查询。它依赖项目启用相应 publication 和访问策略；存在订阅代码不等于跨用户实时更新一定已配置成功。

不要从旧文档重新复制 avg(所有 accuracy_pct) 的 leaderboard SQL，它会覆盖 migration 014 的正式成绩规则。

## 7. 客户端和后台能做什么

客户端可以读自己的 visit/attempt/event、上传任务与录音；偏好表允许管理自己的偏好。新的核心写入由 API 或受控 RPC 执行，不能依赖浏览器直写多张表实现事务。

migration 014 撤销 challenge_recordings、play_events_raw、user_play_logs 的客户端直接写权限，旧裸 log_play_event 也不再是客户端入口。历史 helper 使用 log_play_event_atomic，将终止事件和汇总放进单次调用；backend 管理的 cycle 由 finalize 专门负责。

后端使用独立 PostgreSQL LOGIN，归入 `supabase/backend_role.sql` 定义的 pianokt_backend 权限组。该组具有明确表权限/RLS policy，没有 BYPASSRLS 或 superuser。浏览器不能持有这个连接串或加入这个权限组。

worker 的 /pubsub 由 Cloud Run IAM 限制为 Pub/Sub push 身份；应用本身没有另加独立 OIDC 校验中间件，不能把这个进程直接公开暴露。

## 8. 数据库升级入口

- 001–008：旧录音、白名单、资料、播放事件与汇总。
- 009：新上传任务、outbox、learner key、目录/偏好和推荐。
- 010：backend 写原始事件和汇总的权限补充。
- 011：逐按键 telemetry 文件身份。
- 012：日本时间展示视图。
- 013：visit/attempt 活动上下文。
- 014：最终提交身份、worker lease token、正式评分和原子旧事件 RPC。

`000_run_this_first_save_challenge_recording.sql` 是历史修复脚本；`migrations/sss` 是临时查询，不是应顺序执行的正式迁移。已有项目先核对实际 schema，再按缺少的迁移升级；不要用通配符盲跑整个目录。

新旧 worker 与权限变化需要协调发布，具体顺序见 [部署手册](DEPLOYMENT_AND_ROADMAP_ZH.md)。

## 9. 当前页面能力与排查

录音页目前有列表、MIDI 下载和演示推荐入口。后端已经有状态查询和 result 下载接口，页面尚未接入完整对齐状态/结果查看器。

- 登录后不能上传：检查前端 API URL、token、后端白名单检查与 CORS。
- 登陆成功但没有资料：检查 003/004、profiles、RPC 错误。
- Avatar 403：检查公开读取方式、桶配置和个人目录写策略。
- PUT 失败：检查签名、Content-Type、generation 条件与 GCS CORS。
- 保存失败：检查 IndexedDB 队列、create/finalize 的冲突原因；用相同内容重试。
- 已保存但没有正式分数：查 outbox/relay、worker 状态和质量结果。
- 另一账号能否下载：应验证 API 返回 404/RLS 隔离，不能仅测页面跳转。

真实 GCS、Supabase 角色和跨用户访问仍需部署环境验收。本地测试记录见 [验证记录](VALIDATION_ZH.md)。
