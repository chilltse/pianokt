# PYKT_INTEGRATION — V3 当前入口

当前正式说明：[V3 指南](DATA_ARCHITECTURE_V3.md)。旧版内容保存在 [历史归档](archive/pre_v3/PYKT_INTEGRATION.md)，仅供查阅，不可用于部署。

PyKT 继续使用现有模型词表、KC 六维输入及 1 - reject_reason.event 标签。在线数据只来自当前正式 V3 online Run；research Run 不自动同步。学生状态使用 pianokt:v3 命名空间，旧状态不覆盖。增量同步来源变化时拒绝继续，须在隔离的新状态中显式重建。候选曲库仍以 Segment 为单位。开发验收命令和模型兼容性细节见历史归档；执行时必须使用 DATA_ROOT 和 V3 状态命名空间。
