# 本地应用与开发检查

当前产品入口见 [项目 README](../README.md)，启动、配置、演示与恢复操作见 [运行说明](../docs/runbook.md)。旧版“第几步”学习记录归档至 [学习历史](../docs/local-lab-learning-history.md)，不再作为当前页面操作指南。

## 模块分工

| 模块 | 职责 |
|---|---|
| app.py | 三页签、表单、商品与本地检查展示 |
| catalog.py / quotation.py | 读取模拟目录、校验价格档位、Decimal计算 |
| business_checks.py / payment_ratio.py / check_report.py | 付款完整性、比例及报告 |
| rfq_check.py / rfq_panel.py | RFQ材质预检与界面 |
| payment_wording.py / payment_wording_panel.py | 有限英文付款表述检查 |
| deepseek_client.py | 固定官方端点、生成提示词、响应校验与静态错误 |
| dialogue.py / dialogue_panel.py | 数量和付款变更提取、预览确认 |
| revisions.py / revision_panel.py | 请求上下文、版本快照、比较和下载 |
| history_recovery.py | 两份已核实固定归档恢复，SHA校验 |
| ui_loader.py | 按文件指纹刷新相关模块 |
| schema.py | 学习用结构化输入校验 |

`ai_panel.py` 保留早期练习实现；当前 app.py 经 ui_loader 使用 revision_panel.py，不应把旧面板当作当前入口。

## 本地离线检查

在项目根目录使用项目解释器。下列模块分别在独立进程运行；AI相关检查使用模拟响应，不消耗真实接口额度。AppTest是组件交互验证，不代表真实浏览器或真实模型调用通过。

```powershell
& '.\.venv\Scripts\python.exe' -m local_lab.verify
& '.\.venv\Scripts\python.exe' -m local_lab.verify_quotation
& '.\.venv\Scripts\python.exe' -m local_lab.verify_business_checks
& '.\.venv\Scripts\python.exe' -m local_lab.verify_ai
& '.\.venv\Scripts\python.exe' -m local_lab.verify_revisions
& '.\.venv\Scripts\python.exe' -m local_lab.verify_dialogue
& '.\.venv\Scripts\python.exe' -m local_lab.verify_payment_ratio
& '.\.venv\Scripts\python.exe' -m local_lab.verify_check_report
& '.\.venv\Scripts\python.exe' -m local_lab.verify_payment_wording
& '.\.venv\Scripts\python.exe' -m local_lab.verify_rfq
& '.\.venv\Scripts\python.exe' -m local_lab.verify_workspace_ui
& '.\.venv\Scripts\python.exe' -m local_lab.verify_history_recovery
& '.\.venv\Scripts\python.exe' -m pip check
```

既有验证：三工作区47项检查通过，后续恢复/对话/RFQ相关14项检查通过；记录位于 docs/verification/workspace-ui-tests.log、round2-recovery-tests.log 等。不要将不同轮次的重叠检查相加为唯一测试总数。Windows上单进程合并测试曾在AppTest事件循环初始化停滞，因此采用独立进程；未宣称根因修复。

2026-10-30此次仅整理文档，不据此宣称重新运行了全部测试。真实调用的三版导出及核对结论见 [演示证据](../docs/verification/demo-two-rounds-review.md)。

## 开发注意

- 改模拟商品名称和价格：编辑 planning/fixtures/products.json，保留合法JSON及schema，再用页面重新读取。变更模拟数据可能使原演示金额不再匹配，先保留原文件。
- 改模型与提示词：deepseek_client.py；理解修改的约束见 dialogue.py。当前页面没有服务商切换选项。
- 修改Python后以实际运行服务结果为准；如需重启先下载会话历史。不要仅以新进程测试通过证明旧服务已经更新。
- 归档用于证据与固定恢复，不修改原始记录或校验值来伪造版本连续性。V3归档尚未接入恢复按钮。
- 密钥只通过本机页面输入；不要放入源码、测试文件、截图或日志。
