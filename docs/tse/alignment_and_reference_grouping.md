# Alignment 与 Reference Grouping 实施记录

Reference Grouping 1.0.0 是 Alignment 与 KC Labeler 1.2.0 的共同上游。独立参考提取不再实例化 AlignmentConfig。KC 数学定义未改动。

配置位于 alignment/reference_grouping/versions/v1_0_0.json 与 alignment/alignment_policies/versions/v1_0_0.json。Reference 固定首音锚点窗口 30ms，Performance 50ms，代表时间均为中位数。原音符身份和时间保留。观测范围按完整分组的首音选择，保留既有结束时间加 Reference 窗口容差。

Alignment 1.0.0 使用 1000ms 候选窗口、50ms Perfect、200ms Correct、300ms 错音替换与额外音附着窗口。部分 pitch overlap 后剩余错音也受 note-level 窗口约束。前端构建时导入同一 JSON，使用 song-time 毫秒；在线逐音反馈与离线和弦匹配并非所有竞争情况下等价。

CLI: `pianokt-data extract-reference INPUT --output OUTPUT --reference-grouping-version 1.0.0 --kc-labeler-version 1.2.0`。Python 通过 AlignmentConfig 选择 Alignment 和 Reference Grouping 版本。未知版本拒绝。历史 v3 不能通过重标记为 1.0.0 重现；明确请求 v3 会报错。未声明版本的旧 attempt 使用新默认处理，产生新配置身份；不覆盖历史对象。

新增版本必须增加不可变配置并扩展注册解析，不能修改已发布文件。Alignment 参数变化需重生成匹配和 response 数据；Reference Grouping 变化同时要求重生成参考 KC 与数据集；KC 变化需要重生成特征与相应训练数据。纯 Reference 内存缓存包含 MIDI hash、Reference Grouping 与 KC 版本，不含 Alignment 版本。训练导出拒绝混合 Alignment/Grouping 语义。

## 尚待完成与验证范围

本记录不代表所有设计要求已完成。持久化推荐候选缓存、完整 KT 模型兼容性证明、Bronze/Silver/Gold 显式版本列、跨语言 golden fixtures、系统 synthetic error-injection 报告以及全部前端场景验证仍需补齐。当前不应把新结果直接用于既有模型而忽略兼容性验证。snapshot 缺失的完整上游上下文无法恢复。

完整 Python 测试因环境缺少 torch 在收集阶段中断。可运行子集和前端构建结果见交付报告。未部署、未重新处理生产数据、未修改生产数据库。
