# CrossBot 智能体平台配置说明 (Platform Configuration Export)

本项目采用 **"纯代码工程架构 (Code-Native Architecture)"**，不依赖第三方低代码平台（如 Coze/Dify），所有的智能体配置、知识库与工作流均已通过代码完全固化，以保证极高的企业级安全性与自定义能力。以下为系统核心配置导出说明：

## 1. 模型服务配置 (Model Configuration)
- **协议标准**：兼容 OpenAI 标准 API 协议。
- **模型路由**：系统底层支持动态多模型调度（视觉模型处理单证，文本模型处理询盘与文案）。
- **参数设置**：Temperature 默认设置为 0.3，以保证商务回复的严谨性。

## 2. 智能体角色与任务配置 (Agent Roles & Tasks)
- **W1 客服智能体**：配置于 `workflows/inquiry.py`。
  - 角色：资深外贸业务员。
  - 任务：多语言意图识别、防幻觉回复生成、订单字段提取。
- **W2 运营智能体**：配置于 `workflows/listing.py`。
  - 角色：跨境平台运营专家。
  - 任务：4 大平台（Amazon/TikTok/等）风格自适应生成、违禁词规避。
- **W3 单证智能体**：配置于 `workflows/sales.py` 及 `documents/` 目录。
  - 角色：严谨的外贸单证员。
  - 任务：文档 OCR 解析、关键字段提取、配合 Python 进行交叉比对。

## 3. 知识库配置 (Knowledge Base)
- **存储方式**：本地 Markdown / JSON 文件存储，通过 `knowledge/` 目录进行管理与版本控制。
- **包含内容**：目标国合规要求（如加州 65 提案、TSCA）、平台违禁词库、产品参数档案。
- **检索方式 (RAG)**：系统在生成前，会基于精确的关键词与语义匹配，从本地知识库抽取上下文注入 Prompt。

## 4. 工作流编排 (Workflow)
- 通过 Python 类与函数（`core/` 与 `workflows/` 模块）严格编排了 **“输入 -> 提取 -> 检索 -> 生成 -> 规则校验 -> 反馈”** 的全链路状态机。