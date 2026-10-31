# README 与运行说明核对

日期：2026-10-30。用户批准整理 README 和运行说明。

## 文件

- 根目录 README.md：产品定位、快速启动、两工作流、架构、演示证据、规则边界和交付状态。
- docs/runbook.md：本机启动、新机环境准备、模型配置、两轮演示、备份恢复和排障。
- local_lab/README.md：当前开发入口与离线检查命令。原学习说明完整保存在 docs/local-lab-learning-history.md。
- docs/deepseek-setup.md：增加历史状态说明，指向当前页签和运行手册；保留原始学习记录。

## 实际核对

- 对照 start-local.cmd、app.py、revision_panel.py、dialogue_panel.py、rfq_panel.py、deepseek_client.py、history_recovery.py 和模拟目录核对命令、按钮名、输入及恢复限制。
- 当前解释器实际返回 Python3.12.14 / Streamlit1.66.1 / Pydantic2.14.1；pip check 返回 No broken requirements found。
- 4份文档中的15个本地Markdown链接目标存在。
- 两份恢复归档的SHA256均与代码一致；最新V3归档存在，但未接入页面恢复按钮。
- 本次未改业务代码、未重启服务、未读取密钥、未调用模型，也未重跑全部业务测试。新电脑安装、README逐步人工验收及现场网络未在本轮实测。

下一步整理配置／调用材料、数据来源与第三方声明；海报与3分钟讲稿随后完成。未上传或发布材料。
