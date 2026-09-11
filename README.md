# [pianoKT](https://www.pianokt.com/)

A free and open-source webapp for learning to play Piano. It will analyse your playing and give you the most personalized recomendation and feedback. Plug in your MIDI keyboard to play. See full details on the [website](https://www.pianokt.com/about).

## 本次后端扩展

新录音直接上传私有 GCS，Supabase 保存在线状态，Python Worker 执行版本化 MIDI alignment，定时任务将结果与用户事件转换为 Delta Bronze / Silver / Gold。推荐接口已接通，当前模型为明确标识的演示黑盒。

- [代码级教学文档](docs/PIANOKT_TEACHING_GUIDE_ZH.md)
- [本地运行、GCP 部署与下一步蓝图](docs/DEPLOYMENT_AND_ROADMAP_ZH.md)
- [相对原工程的变更清单](docs/CHANGES_ZH.md)
- [验证结果与边界](docs/VALIDATION_ZH.md)

先按部署文档运行本地合成 MIDI 示例，再在测试云项目验收。不要将下载完成视为云端已部署；真实训练模型尚未包含。
