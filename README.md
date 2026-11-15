# [PianoKT](https://www.pianokt.com/)

PianoKT 是浏览器钢琴练习应用。前端提供 MIDI 输入、乐谱播放和实时反馈；Challenge 证据经不可变 Raw 上传，由 Python Worker 生成版本化 Silver，PostgreSQL Registry 记录正式提交，按需导出 Gold。

当前数据架构为 V3，无 Spark、Delta、定时物化或 Lake Bucket 运行依赖。普通 Practice 支持官方完整曲目与 Segment；Challenge 和推荐决策使用 Segment。模型输入、Alignment、KC 和评分数学语义保持不变。

- [架构与数据流](docs/DATA_ARCHITECTURE_V3.md)
- [数据契约](docs/DATA_CONTRACT_V3.md)
- [数据导出与研究重处理](docs/DATASET_EXPORT_GUIDE.md)
- [生命周期与排除](docs/DATA_LIFECYCLE.md)
- [本地验证、部署与切换](docs/DATA_ARCHITECTURE_V3_CUTOVER.md)

前端：`npm install`，`npm run dev`，`npm run check-types`，`npm run build`。
后端：Python 3.13，安装 `backend/requirements.lock` 后 `pip install --no-deps -e backend`。设置 DATABASE_URL、DATA_ROOT、PIANOKT_ENV；仅连接隔离数据库进行测试。

部署必须应用 migration 024，并受控排空旧在途 Attempt。旧用户、曲库、活动事实和产品历史保留；旧 Alignment 不进入 V3 研究数据。仓库实现和本地验证不代表已部署生产环境。
