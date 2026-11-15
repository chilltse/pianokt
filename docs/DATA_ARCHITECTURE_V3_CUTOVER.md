# V3 Clean Cutover / 云资源退役

本手册是管理员执行计划，不是已执行记录。本次开发不运行 apply、生产 SQL、Bucket 删除或历史回填。

1. 备份数据库 schema、Terraform state 和部署配置。确认原始 MIDI、曲库、账号、活动事实均保留。盘点旧非终态 Attempt 和 Pub/Sub backlog。
2. 暂停新的上传入口；旧 Worker / Relay 在旧契约下排空 CREATED/UPLOADING/ACCEPTED/VALIDATING/PROCESSING/RETRY。未完成上传须用户重试或逐条显式取消，记录原因；不能把所有 Attempt 批量 FAILED。确认无旧在途 lease 后停止旧 Worker 投递。新旧 Worker 不同时消费同一队列。
3. 在隔离环境应用 `024_data_architecture_v3.sql`，再经审核应用生产。既有行标记 legacy；既有 READY、challenge_recordings 和 Product Results 可继续作为产品历史显示，但旧研究结果不进入 V3 Gold / PyKT。旧详情 result 下载不作为 V3 导出入口；管理员可归档旧对象。不要回填 Registry。
4. 所有运行配置将 RAW_ROOT 一次性改为 DATA_ROOT，删除 LAKE_ROOT / CHECKPOINT_ROOT / ENABLE_SPARK_EVENTS。DATA_ROOT 保留原 Data Bucket 名；新对象使用 raw/silver/gold 前缀。开发、测试和生产根路径及服务环境隔离。更新本地未跟踪 `.env.local`，不把密钥提交到仓库。
5. 构建普通 `backend/Dockerfile` 镜像并部署 API / Worker / Relay / Exporter。迁移与新代码一起切换；新 Worker 不认领 legacy 非终态。禁止切换后直接回滚旧 Worker 处理 V3 路径。
6. 验收一条真实 MIDI：create / upload / finalize / Worker / Registry / READY / 分数 / 两类 Gold / hash 验证；检查租约重试、损坏文件、跨环境、PyKT V3 新命名空间。验收后恢复浏览器入口、Pub/Sub 和 Relay。Exporter 仅按需执行。

## Terraform

保留地址：`google_storage_bucket.data["raw"]`、`google_cloud_run_v2_service.app["api"/"worker"]`、`google_cloud_run_v2_job.job["relay"]`、对应 service accounts、Pub/Sub、Artifact Registry、现有 export count 地址。Relay 的 for_each 只移除 pipeline，不改名。

目标配置不再声明 pipeline Job / Scheduler / service account、专属 IAM、lake/checkpoint buckets。**不要直接对旧 state apply 删除后的配置**：先备份 state，审核旧 pipeline 的 deletion_protection，显式解除保护并退役计算资源。lake/checkpoint buckets 及其对象保留，由管理员从当前运行 stack 的 state 分离（审查 `terraform state rm 'google_storage_bucket.data["lake"]' 'google_storage_bucket.data["checkpoint"]'`），登记在归档资源清单；state rm 不是云资源删除。实际删除另行审批。Data Bucket 保持 force_destroy=false、prevent_destroy=true；Terraform state bucket 不在此次变更范围内。

更新未跟踪 terraform.tfvars，去掉 spark_image / lake_pipeline_paused / pipeline_schedule，并按 terraform.tfvars.example 校正 frontend_origins。先 fmt、validate，再在具备只读凭证的管理员环境执行 plan；不应出现任何保留地址的 destroy / replace。本地 plan 失败或缺少凭证时不能宣称云端资源已安全退役。

API 读 Raw/Silver、创建 Raw；Worker 读 Raw/Silver（幂等恢复）、创建 Silver；Exporter 读数据、仅创建 Gold；Archive 仅创建 raw/events；运行账号不授予对象删除权限。Signed URL 的 create-only generation 条件和现有签名账号保持。

## 发布前运行

```bash
python -m pip install -r backend/requirements.lock
python -m pip install --no-deps -e backend
python -m pytest backend/tests -q
npm run check-types
npm run build
docker build -t pianokt-backend:v3 backend
terraform -chdir=infra/gcp fmt -check
terraform -chdir=infra/gcp validate
terraform -chdir=infra/gcp plan
```

完整测试的 PGLITE_MODULE / PIANOKT_TEST_POSTGRES_DSN 指向隔离测试数据库；实验模型测试另需 torch 和外部模型环境，不能指向生产数据库运行测试。
