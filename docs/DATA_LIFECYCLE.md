# V3 数据生命周期

曲目 unpublish 仅影响新产品展示、练习、推荐候选；不删除历史。Song / Segment / Learner / Attempt 稳定身份用于定位所有派生结果，不通过标题匹配。

管理员可在 `piano_data_exclusions(source_environment,scope,identity,reason)` 登记研究排除。scope 为 learner / attempt / song / segment / dataset。普通运行账号只有读取权限。排除表无级联外键，原始事实删除后 tombstone 仍保留，避免重处理或重建重新引入。

后续 Gold source 和活动 metadata snapshot 均应用排除；reprocess 在读 Raw 前及提交事务中再次校验。dataset scope 的 identity 是 dataset_version，阻止重新发布和官方下载脚本消费该版本。已下载的离线副本需要数据管理者通知撤回，不能声称远程删除已发生。

永久删除须另行审批，按稳定身份清点：Supabase 活动 / 上传 / 业务记录，Raw 的已登记 keys / generations，Registry 引用的 Silver，所有发布 manifest 的 source_runs，KT 历史 / journal / 模型状态。先登记 tombstone、停止相关任务，再撤回受影响 Gold，删除授权范围中的数据，复核剩余引用，最后使用新的来源集合重建数据集或 KT 命名空间。Registry 的 attempt 外键级联删除不代表已删除对象存储或已发布 Gold。

manifest source_runs 包含 learner_key、attempt_id、song_id、segment_id、run ID 和结果 digest，支持受影响数据集清点。应保留发布 manifest 清单作为研究交付凭证。当前没有自动跨存储永久删除工具或完整治理平台；普通 API、Worker、Exporter 没有对象删除权限。
