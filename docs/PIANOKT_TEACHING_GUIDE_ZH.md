# 跟着一次 Challenge 读懂代码

更新日期：2026-11-03。本文用一个假想但符合当前实现的例子串起源码：学生打开网页，练习一首歌，暂停一次，继续，然后提前退出。它不是真实学生数据。

整体地图见 [架构总览](ARCHITECTURE_ZH.md)，字段和接口查询见 [实现参考](PIANOKT_SYSTEM_REFERENCE_ZH.md)。本篇只沿一次演奏前进。

## 1. 打开网页：先建立访问，不是先创建录音

先读 `src/pages/providers.tsx` 和 `src/features/auth/context.tsx`。

AuthProvider 维护登录状态，PlayerProvider 提供播放器。登录状态交给 `features/activity/index.ts:setActivitySession`，它在整个站点范围维护访问，页面内的路由切换不重建 visit。

例如学生先看首页、排名，再打开 Challenge，三个页面仍属于同一个访问。此时数据库可能已经有 student_visits，但还没有一次实际演奏，也没有 MIDI 上传任务。

`ActivityTracker` 使用 sessionStorage 记住当前访问，localStorage 保存待发送活动包；心跳每 30 秒发送。刷新可以延续访问，但内存中的录音无法凭 sessionStorage 自动恢复成一段连续演奏。

## 2. 选歌：浏览器获得统一的乐谱结构

再读 `features/data/index.tsx`、`features/parsers/parse-midi.ts` 和 `features/player/player.ts`。

曲库中的 MIDI、本地 MIDI 或 base64 MIDI 先被解析成 Song。播放器根据 Song 和 SongConfig 知道每个音应该在哪个时刻出现、哪些轨道属于左右手、是否移调、是否选择片段。

这一步主要发生在浏览器，尚未请求后端对齐。屏幕上即时出现的分数也由播放器计算。

## 3. 点击开始：倒数和实际演奏分开

读 `pages/challenge/page.tsx` 与 `useChallengeAttempt.ts`。

点击开始时页面冻结本次 assigned range、手部和移调配置。倒数结束、歌曲 transport 真正开始后，才记录 play_started，建立 attempt 上下文和开始时间。

因此，取消倒数不应该制造一次只有 pause/resume、没有真实开始的演奏。

新 Challenge 使用同一个 UUID 表示 attempt 和旧播放周期 session。visit ID 是另外一个值，用于把这次演奏归到网页访问下。

`recordChallengeEvent` 生成稳定 event ID 和 event sequence，再调用 `recordAttemptEvent` 上报活动。服务器入口是 `activity.py:save_activity`。

## 4. 学生弹键：保留两种证据

读 `features/midi/index.tsx` 和 `useSegmentedRecordMidi.ts`。

每个按键既参与生成 performance MIDI，也保留在按键审计数据中。审计包括键的音高、力度、on/off、浏览器时钟、MIDI event timestamp 和对应歌曲位置。

两份证据各有用途：MIDI 可下载、播放和复核；审计记录能更清楚地解释暂停、继续、输入时间以及歌曲位置。

这里最容易混淆三个量：

- 现实中“2026 年某天几点开始”，是 UTC 时间戳。
- “歌曲播放到了第 12 秒”，是 player song time。
- “浏览器单调时钟当前读数”，是 performance.now 一类的采集时间。

它们不能直接替换使用。

## 5. 暂停 10 秒再继续：仍是同一个 attempt

useChallengeAttempt 停止有效演奏时长累计，recording hook 记录 pause segment，活动时间线记录 paused。继续后记录 resumed，并恢复录音和累计。

现实经过 10 秒，但乐谱位置没有因此前进 10 秒。后端对齐新录音时优先用歌曲时间，所以暂停不会自动制造一串晚 10 秒的节奏错误。

这个暂停点最终可在 student_attempt_events 中查到；transportSegments 也保留录音时间结构。这两份记录分别服务于行为时间线和录音对齐审计。

## 6. 提前退出：先取终止位置，再停止播放器

Challenge 的终止流程先捕获歌曲位置、统计、配置和录音，再调用 player.stop。否则 stop 重置位置后，可能把整次演奏错误保存成停在 0 秒。

`referenceSnapshot.ts` 用实际 observed range 导出参考 MIDI：例如原本要求练 0–60 秒，只练到 20 秒，则参考快照只覆盖观察到的片段，assigned range 仍单独保留。

封存包包括：

```text
本次录音包
├─ performance MIDI
├─ observed reference MIDI
├─ 按键事件 + start/pause/resume/stop segments
├─ 冻结的练习设置与范围
├─ 完整 play_started/paused/resumed/exited cycle
└─ 原始 owner、attempt、visit 上下文
```

这里的“完整”表示这次实际尝试的证据完整，不表示整首曲子已经弹完。is_played_in_full 只认 finished，提前退出不会因达到 98% 而被改成完成。

## 7. 上传：先有本地完整包，再申请远端地址

读 `challenge-history/upload-queue.ts` 和 `gcs.ts`。

IndexedDB 中一个 owner + attempt ID 对应一份封存记录，保存成功后才执行上传。先 POST /attempts，再直接 PUT 到 GCS，最后 POST /finalize。

在正常新录音中，GCS 文件是两份 MIDI 和一份 performance-events.json。每份都有 SHA-256；create-only 条件阻止同 key 被替换。

网络失败时留着队列；同账号的登录恢复、online 事件和定时器会继续重试。切到另一个账号不应把录音重新归属给新用户。

如果返回响应丢失，服务端可能已经成功提交。用同 ID 同内容重试，目的是确认已有结果，不是多建一条录音。

## 8. 最终提交：一次事务把相关表连起来

读 `api.py:finalize`、`upload_contracts.py`、`upload_service.py`。

API 先核对对象是否存在、摘要和大小是否一致、cycle 是否有效，以及活动序列是否匹配。

数据库事务中，一次写入 UPLOADED、finalization、录音列表、原始播放事件、播放汇总和 outbox。原始事件 trigger 也会生成 practice.event outbox。

任何一步失败，这次最终提交的数据库修改一起回滚。早已保存的活动历史不会被删掉；数据库外已经上传的文件也可能暂存，但不能因此伪装成已完成业务。

/activity 上报和 finalize 共享 attempt 锁，防止最终校验与活动追加相互穿插。最终提交之后，不能再给 sealed attempt 追加新活动 ID。

## 9. 异步处理：outbox 先保证消息有出处

读 `worker.py:relay` 和 `worker_api.py`。

保存录音时没有把数据库提交和 Pub/Sub 发布硬塞进一个假想全局事务。业务事务先留下 outbox；relay 之后发布消息，成功后标记 published_at。

主 topic 一路归档事件到 GCS，一路把 performance.uploaded 送到私有 worker。worker_api 启动子进程执行 `pianokt-data worker <attempt-id>`。

当前默认 relay 调度不是连续运行：Asia/Tokyo 10:00/23:00，且调度默认暂停。实际等待取决于是否启用、频率和积压。因此上传成功后，后端对齐不会保证立即完成。

## 10. 对齐：把该弹的和实际弹的对上

读 `worker.py:process`、`alignment/service.py` 与 `alignment/time_guided.py`。

worker 领取任务，写 PROCESSING 和新的 lease_token，读取输入并再次核对摘要。它推导观察范围和快照原点，再计算本次确定性的 run ID。

当前 matcher 为 time-guided-chord-monotonic-v3：利用歌曲时间，在有限时间窗口里进行保持先后顺序的和弦/音符匹配。不是旧文档所写的 Parangonar 全曲配对流程。

reference 音符和 performance 音符可能形成：

- match：配对成功，之后仍需看音高与时序是否正确。
- deletion：目标音没有对应演奏，标记漏音。
- insertion：多弹音，不占用目标音的错误码。

提前退出后尚未练到的曲谱尾部不会被整段追加为漏音。

## 11. 结果：先看质量，再看 accuracy

读 `alignment/labels.py`、`events.py` 和 service 的 summary/diagnostics。

音符标签是 0 正确、1 漏音、2 错音、3 节奏错误、4 错音且节奏错误。默认 timing 阈值为 150 ms。

逐 onset 数据把目标音按 `[左手,右手]` 编排，每条 onset 有一个 KC vector 和一个二元 event 错误标签。逐音符标签、时间偏差与音高偏差都与目标 pitches 同形；多弹音另放 extra_notes。

质量不通过仍可能得到一个保留下来的 result，但不能据此计算可信训练标签。READY 仅代表计算结果已经存好。

worker 将 result 写入 create-only raw key，然后锁定 attempt、校验 lease_token，把 READY、summary、正式评分和 alignment.completed outbox 一起提交。过期的旧 worker 不得覆盖后来的租约结果。

## 12. 入湖：两条输入、两套完成标记

读 `pipeline/jobs.py`、`spark_events.py` 和 `tables.py`。

alignment result 由 jobs.py 使用 delta-rs/PyArrow 整理；Pub/Sub 归档事件由 Spark AvailableNow 处理。

理解 Bronze/Silver/Gold 可以用三个问题：

1. 当时收到的来源是什么？查 Bronze。
2. 每个事件/音符被整理成什么明细？查 Silver。
3. 哪些结果质量可信、能做统计？查 Gold。

对齐结果最后写 ops/completed_runs；事件流最后写 ops/practice_event_commits。读多张正式表使用 Tables.snapshot 固定提交集合，不能只看某张物理表有没有新行。

已完成 run 保持不可变。同一 attempt 的不同 run 都能保留，消费方要选择版本。物化规则改变时，在新湖仓路径重建、验收后切换。

## 13. 导出：现在才计算训练文件中的顺序号

读 `pipeline/attempts.py` 与 `selection.py`。

导出先在 repeatable-read 中取数据库访问/演奏/事件上下文，再选择已入湖完成、早于 cutoff、质量通过、非 waiting 的结果，确认演奏已经结束且汇总完整。

visit 和 attempt 根据学生的真实 started_at 转 UTC 排序。并列时用 visit ID、访问内 sequence、attempt ID 保持确定性。先给全部可见历史编号，再筛训练质量，所以 attempt_index 缺号是正常情况。

`days_since_signup = (attempt.started_at - 注册时间) / 一天`。它可以是小数；开始早于注册会报错。

一份 attempt 数据集包含主数据、visit 元数据、attempt 元数据、活动节点和 index_map。内部 ID/开始结束时间不放入主 attempt 行。

intent 固定整份文件的哈希计划，manifest 最后发布。读取者使用 read_bundle 验证摘要。半成品文件不能当作完整数据集。

## 14. 推荐与训练在哪里

读 `inference.py`、`api.py:recommendations` 和前端 Recommendations。

后端可以把最近有效演奏的完整对齐结果交给推荐器；当前 DemoRecommender 实际按难度和偏好打分。录音页可发起推荐并记录曝光/点击。

export-attempts 和 export-training 生成训练输入，尚未接自动训练或正式能力预测。只有积累真实数据、定义训练/评估拆分并接入模型后，这部分才会成为知识追踪和个性化模型系统。

## 15. 调试时先定位到哪一层

- 页面演奏/时间不对：Player、MIDI 输入、录音 hook。
- 暂停/退出节点缺失：activity 队列、/activity、student_attempt_events。
- 录音未保存：IndexedDB 队列、create/PUT/finalize 返回、CREATED 状态。
- 保存了却一直没对齐：outbox、relay、Pub/Sub push、worker 日志。
- READY 分数不可用：summary/diagnostics 的质量原因。
- 有 JSON 却没有 Gold：pipeline、quality、ops/failures/completed。
- 导出为空：cutoff、活动上下文、质量/waiting、完成标记和 run 选择。

一次演奏从哪里出问题，就沿着对应层的实际记录查，避免把“录音存在”“对齐成功”“可以训练”当成同一个状态。
