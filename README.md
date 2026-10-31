# CrossBot · AI+出海协作工作台

本分支整合 A 的账号/Case/权限、B 的 W1/W2 和 C 的 W3 单证工作台（包含其分支已集成的确定性核验）。W1/W2 可使用本地模拟或明确配置的真实模型；输出是待人工复核草稿，内置有限规则和模拟知识不代表完整合规审查。

## 当前集成边界

### 动物台灯 W1/W2 固定演示资料

可从 Case 面板导入“动物台灯固定演示案例”，或在 W1/W2 点击对应的载入按钮。SKU `DEMO-ANIMAL-LAMP-001`、订单 `CASE-DE-ANIMAL-LAMP-001`、虚构供应商、产品参数和交易均为单一版本的自编演示数据。金额、箱数和重量由 Decimal 校验；不影响旧德国台灯 Case。内部风险词库位于 `knowledge/prohibited_terms.json`，W1 每次修订和 W2 每次生成都会重新检查，返回规则编号/版本和修改建议。词库不是官方政策，未命中也不代表合规。操作步骤、固定询盘和验收口径见[动物台灯演示指南](docs/demo_case/动物台灯演示指南.md)。

- W1/W2使用A的登录、角色权限、Case分配、生成结果审计与Case级持久化。
- W1支持询盘候选识别、两轮修订、独立客户模拟、风险提示和结果导出。
- W1“回复输出语言”与 W2“文案输出语言”决定生成内容的语言，支持英文、德文和中文；与输入原文语言无关。载入案例仅填充业务输入，保留当前语言、平台和历史结果，不自动生成。模拟模式不执行翻译。
- W2严格只生成用户选择的平台，并显示模拟知识来源和待确认项。
- API Key仅保留在浏览器会话；真实模式发送前需用户明确确认。
- 历史聊天格式会留在“迁移自旧版的历史”区域，不伪装成新版结构化修订。
- W3 已接入当前登录、Case 和数据库，支持原件上传/版本、PDF/Office 解析、人工填报和复核、确定性核验、预览及多格式导出。启用方式及限制见[W3 集成说明](docs/w3-integration.md)。
- 新工作台采用可展开模块侧栏；「资料与知识」展开后选择产品资料、知识库、风险词库。管理员可分别上传、提交审核与发布版本；上传资料尚未自动接入 W1/W2 检索和风险扫描。真实与模拟 Case 分区显示，断网测试使用独立本地结果。详见[工作台 UI 说明](docs/workbench-ui.md)。
- 真实外部搜索供应商以及 W1/W2 表单的附件选择仍需后续接入。

## A+B 集成行为

- 登录和服务端角色核权；管理员创建岗位账号并分配 Case。
- Case 与 W1/W2 请求、AI生成版本、模拟客户记录、人工修订和导出结果关联。
- W1 支持询盘候选识别、两轮修订、独立客户模拟、风险提示和业务记录导出。
- W2 严格只生成所选平台，并显示模拟知识来源和需人工确认项。
- W1/W2 状态按 Case 写入本地 SQLite；刷新或重启后可恢复；API Key仅保存在浏览器会话。
- 历史 A版简单聊天消息保留在“迁移自旧版的历史”折叠区，不伪装成 B 新版的结构化修订。
- 真实模型调用需用户选择真实模式、设置供应商与密钥并确认发送资料/额度。

W3 文件解析与单证页已接入；资料治理页面已接入原有版本服务，上传资料自动用于 W1/W2 仍需后续接通。外部网络搜索供应商和 W1/W2 附件选择仍需后续接入。下方 B 阶段描述保留历史上下文，当前 W3 状态以集成说明为准。

当前分支 `feature/w1-w2-platform` 是洪彬斌负责的B阶段实现，基于公共契约分支。提供W1询盘回复/两轮修订和W2按平台生成，并接入带来源的模拟知识资料。使用AI辅助开发，用户逐步运行验收。

本次集成将该工作流接入A的登录、Case授权和本地数据库。W1修订历史与W2各平台结果按Case保存；真实模型Key仍仅保留于浏览器会话。原B独立页面组件测试保留在 `tests/w1_w2_ui_harness.py`，主入口测试使用 `tests/test_database_integration.py`。

## 按 V3.0 继续更新

用户新规划已原样保存到[规划 V3](docs/plans/project-direction-v3.md)。新增[德国台灯固定案例](docs/demo_case/b_workflow_demo.md)、W1意图/候选识别、独立模拟客户和W1/W2生成后词项/数值风险检查。每次识别、模拟客户或回复在真实模式下单独调用API；默认模拟，无自动调用。8条模拟知识不等于正式知识发布。当前95项离线测试通过（首批69项及后续26项）；用户已验收V3资料载入，真实候选识别曾报错并已修复定位机制，仍待用户复验，客户模拟也尚待用户调用验收。

W1/W2可点击“载入德国台灯演示资料”，只改表单、不删除旧历史或创建持久Case。候选结果使用core.ExtractedField；模型引用原文，由代码计算位置，找不到或无法区分的引用拒绝整批，不自动确认。兼容旧索引但不依赖模型计数字符，见[定位修复](docs/b-inquiry-evidence-fix.md)。W2现在显示风险提示，原“Compact design”样例可被设计推断规则标记；原始证据保持不变。

B 已适配 A 的 RequestContext 和 V3 公共服务：模型请求前执行服务端授权，成功结果保存为独立生成版本；附件、搜索与知识治理的接入协议见[A/B V3 接口文档](docs/B_V3接入说明.md)。已同步德国台灯案例数据。本分支不代表W3解析器、真实搜索供应商或整个V3工作台全部完成。

## 本阶段实际行为

- W1使用core.SalesRequest独立传递字段，保留换行和特殊字符。首次生成与修订有明确状态，失败不追加错误草稿。
- W2使用ListingRequest，只生成所选平台；空选阻止提交，缺失/多余平台或非法JSON不保存。
- 模拟/真实模式显式切换，默认模拟。真实调用通过统一HTTPS客户端，错误分类、超时、截断/空输出保护，无自动重试或静默模拟降级。
- knowledge为8条自建模拟资料，按完整演示SKU、平台、国家和日期过滤，显示来源、版本、适用条件。不是向量RAG，也不是官方平台政策或真实供应商证明。
- 已有业务结果可导出JSON、W1/W2 TXT，保留版本/修改要求/来源/模型和引用，不导出模型设置或密钥。W1/W2历史保存到Case数据库；模型Key不持久化。

原main.py、utils、prompts、knowledge_base只保留迁移参考，当前页面不再导入其旧工作流。其他成员的W3/交叉比对功能由团队在各自分支推进；本阶段不宣称全项目或全量合规验收完成。

## 启动

建议 Python 3.12（含旧版可选 OCR）。当前本地环境为 Windows / Python 3.14.6：PDF、Excel、Word 和导出依赖已安装；旧版 RapidOCR 仅对 Python <3.13 安装，更高版本可保存图片/扫描 PDF 并人工录入。全新电脑安装尚未实测。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

打开 http://127.0.0.1:8502/ ，默认模拟，可不填Key验证流程。切真实后新会话默认使用 TokenHubCloud：`https://api.tokenhubcloud.net`、`gpt-6.1-sol`、Responses API；模型可用性、密钥分组和额度必须在该平台控制台核对。也可填写 OpenAI 官方或其他兼容服务地址，手动选择协议。API Key 需在本机密码框输入并确认资料发送/费用；只有点击生成或连接测试才请求。测试只发送固定问候，不发送 Case 资料。旧会话需手动核对原地址。接入方式及本轮定位的 403 客户端标识问题见[TokenHubCloud 接入说明](docs/tokenhub-api.md)。W2 使用 JSON Schema 限定所选平台，仍需网关真实验收。程序不加载 `.env`，也不把密钥写入聊天、源码或测试数据。Responses 结构依据见[官方文本生成](https://developers.openai.com/api/docs/guides/text?api-mode=responses)和[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)。

W1示例产品 `DEMO-LAMP-001`，W2示例产品 `DEMO-CUP-001`。这两个标识和参数都是模拟设定，命中不证明用户描述真实或一致。每次真实调用可能产生费用，手动重试也可能收费。

## 测试与交接

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

首批阶段69项离线测试通过，模型请求使用替身。用户另外完成W1首次/两轮真实修订及W2 Amazon真实生成，原样导出核对见[演示证据](docs/verification/live-20261031/README.md)。发现W2额外使用“Compact design”，没有尺寸依据，已标注人工删除或重写建议，原件未修改。

[阶段交接](docs/b-stage-handoff.md)｜[AI开发约束](AI开发约束.md)｜[目录与协作](docs/directory-structure.md)｜[公共接口](core/README.md)｜[模型配置](docs/b-model-client.md)｜[下载说明](docs/b-business-export.md)。各步历史文档按当时状态保留，以当前README/阶段交接为准。

## 已知边界

所有输出需人工核对，有限词项/数值检查和模拟检索不能保证模型没有额外推断，不代表平台审批、合同确认或法律审查。W1提供本地/模型候选识别，保留原文位置且始终待确认，不自动填回结构化quantity。外部搜索供应商尚未接通；知识管理支持上传与版本流程，但未自动接入 W1/W2 检索。W2当前为单次平台生成。W3 已迁移到主入口，但不会把其机器候选自动作为 W1/W2 的确认事实。

本阶段使用Streamlit界面和Python标准库urllib；core公共契约未变。requirements.txt原有OpenAI SDK/python-dotenv依赖未删除，当前W1/W2不使用它们。旧contributions目录的组件清单仅对应旧个人原型环境，不冒充当前团队完整依赖清单。许可证以各组件上游为准，尚未为团队自有源码新增许可证。

阶段性提交不创建最终 hackathon-final 标签。W3 本轮迁移来源为本地远程跟踪快照 `8d6b947`；没有提交、推送或改写分支历史。最终交付由团队统一确认。
