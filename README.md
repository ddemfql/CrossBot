# CrossBot · AI+出海协作原型

当前分支 `feature/w1-w2-platform` 是洪彬斌负责的B阶段实现，基于公共契约分支。提供W1询盘回复/两轮修订和W2按平台生成，并接入带来源的模拟知识资料。使用AI辅助开发，用户逐步运行验收。

## 按 V3.0 继续更新

用户新规划已原样保存到[规划 V3](docs/plans/project-direction-v3.md)。新增[德国台灯固定案例](docs/demo_case/b_workflow_demo.md)、W1意图/候选识别、独立模拟客户和W1/W2生成后词项/数值风险检查。每次识别、模拟客户或回复在真实模式下单独调用API；默认模拟，无自动调用。8条模拟知识不等于正式知识发布。当前95项离线测试通过（首批69项及后续26项）；用户已验收V3资料载入，真实候选识别曾报错并已修复定位机制，仍待用户复验，客户模拟也尚待用户调用验收。

W1/W2可点击“载入德国台灯演示资料”，只改表单、不删除旧历史或创建持久Case。候选结果使用core.ExtractedField；模型引用原文，由代码计算位置，找不到或无法区分的引用拒绝整批，不自动确认。兼容旧索引但不依赖模型计数字符，见[定位修复](docs/b-inquiry-evidence-fix.md)。W2现在显示风险提示，原“Compact design”样例可被设计推断规则标记；原始证据保持不变。

账号核权、Case关联、附件服务、搜索来源与知识版本发布尚需A/C的公共接口，详见[接口需求与未完成项](docs/b-v3-contract-requests.md)。已同步A最新案例文档943fc47并保留原件，B适配读取其JSON。当前不声称已完成整个V3工作台。

## 本阶段实际行为

- W1使用core.SalesRequest独立传递字段，保留换行和特殊字符。首次生成与修订有明确状态，失败不追加错误草稿。
- W2使用ListingRequest，只生成所选平台；空选阻止提交，缺失/多余平台或非法JSON不保存。
- 模拟/真实模式显式切换，默认模拟。真实调用通过统一HTTPS客户端，错误分类、超时、截断/空输出保护，无自动重试或静默模拟降级。
- knowledge为8条自建模拟资料，按完整演示SKU、平台、国家和日期过滤，显示来源、版本、适用条件。不是向量RAG，也不是官方平台政策或真实供应商证明。
- 已有业务结果可导出JSON、W1/W2 TXT，保留版本/修改要求/来源/模型和引用，不导出模型设置或密钥。历史仅当前会话，无通用导入或自动持久化。

原main.py、utils、prompts、knowledge_base只保留迁移参考，当前页面不再导入其旧工作流。其他成员的W3/交叉比对功能由团队在各自分支推进；本阶段不宣称全项目或全量合规验收完成。

## 启动

建议Python 3.12，已在Windows/Python3.12.14及现有依赖环境验证；全新电脑安装尚未实测。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

打开 http://127.0.0.1:8502/ ，默认模拟，可不填Key验证流程。切真实后在侧栏填写对应服务HTTPS地址、模型和密码框Key并确认发送资料/费用，用户点击生成才请求。默认提供DeepSeek官方地址/模型；其他兼容服务需要自行验证。不加载.env，也不把密钥放入聊天、源码或测试数据。

W1示例产品 `DEMO-LAMP-001`，W2示例产品 `DEMO-CUP-001`。这两个标识和参数都是模拟设定，命中不证明用户描述真实或一致。每次真实调用可能产生费用，手动重试也可能收费。

## 测试与交接

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

首批阶段69项离线测试通过，模型请求使用替身。用户另外完成W1首次/两轮真实修订及W2 Amazon真实生成，原样导出核对见[演示证据](docs/verification/live-20261031/README.md)。发现W2额外使用“Compact design”，没有尺寸依据，已标注人工删除或重写建议，原件未修改。

[阶段交接](docs/b-stage-handoff.md)｜[AI开发约束](AI开发约束.md)｜[目录与协作](docs/directory-structure.md)｜[公共接口](core/README.md)｜[模型配置](docs/b-model-client.md)｜[下载说明](docs/b-business-export.md)。各步历史文档按当时状态保留，以当前README/阶段交接为准。

## 已知边界

所有输出需人工核对，有限词项/数值检查和模拟检索不能保证模型没有额外推断，不代表平台审批、合同确认或法律审查。W1提供本地/模型候选识别，保留原文位置且始终待确认，不自动填回结构化quantity。无流式UI事件、知识外部更新、任意历史恢复或自动持久化；W2尚无多轮修订。

本阶段使用Streamlit界面和Python标准库urllib；core公共契约未变。requirements.txt原有OpenAI SDK/python-dotenv依赖未删除，当前W1/W2不使用它们。旧contributions目录的组件清单仅对应旧个人原型环境，不冒充当前团队完整依赖清单。许可证以各组件上游为准，尚未为团队自有源码新增许可证。

阶段性提交不创建最终hackathon-final标签，不合并其他分支。最终交付由团队统一确认。
