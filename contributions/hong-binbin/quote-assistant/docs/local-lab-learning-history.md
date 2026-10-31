# 本地工具验证环境

用途：逐步学习 Streamlit 网页、数据校验与报价。用户已确认前六步完成，实际导出见 docs/verification/step6-user-history.json；第七步限定数量/付款的自然语言修改待用户验证。不是完整参赛作品。

## 启动

在项目根目录双击 `start-local.cmd`，然后访问 http://127.0.0.1:8501 。窗口中的 Ctrl+C 可停止服务。如果已有服务占用端口，不要重复启动；先检查原服务。

PowerShell 也可在项目根目录执行：

```powershell
& '.\.venv\Scripts\python.exe' -m streamlit run local_lab/app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

页面会显示运行版本，接受 JSON 输入，提供字段错误提示及规范化数据下载。输入改变后，旧校验结果会失效，必须重新校验。页面只检查结构，不判断业务真实性或贸易合规。

## 第二步：修改商品名称

页面顶部新增模拟商品表，数据从 planning/fixtures/products.json 读取。将第一个商品的 name_zh 值从“原色棉布手提袋”改成“洪彬斌的练习布袋”，保存文件，然后点击页面“重新读取商品文件”。只改双引号内的中文，保留引号、逗号和其他字段。第一行应显示新名称。

本步要理解：products.json 存数据；local_lab/catalog.py 读取和检查数据；local_lab/app.py 把数据展示为表格。无需一次理解全部代码。商品名称不会写死在页面代码中，也不需要模型生成。

## 第三步：改变数量，观察金额

刷新页面，在“第三步：数量如何影响货品金额”选择 BAG-001（洪彬斌的练习布袋）。先输入 300 并按回车，应显示单价 1.20 USD、货品金额 360.00 USD；再改为 1000 并按回车，应显示单价 1.05 USD、货品金额 1,050.00 USD。

本步要理解：程序先根据数量选择价格档位，再用单价乘数量。products.json 提供模拟价格，local_lab/quotation.py 负责计算，local_lab/app.py 展示结果。金额用十进制数计算；低于最低订购数量时显示提示，不显示金额。这里只计算模拟货品金额，不含运费、税费或定制费用。

## 第四步：补全付款条件

在页面“第四步：补全一项业务信息”，先看空白输入下的“待补充：付款条件”提醒，再输入“模拟约定：30% 预付款，70% 发货前支付”，按回车。提醒应变为“已填写付款条件；……仍需人工核对”。清空并按回车，缺项提醒会恢复。

付款条件是何时付款、如何分期等约定。local_lab/business_checks.py 实现自定义练习规则 C01（依据本项目模拟案例 T05），检查空白和常见待确认占位词。它不判断实际条款合理性、双方是否同意或法律合规；任意其他非空文字仅标记已填写，不判为正式报价可用。未解析自然语言询盘，未接 AI，也未完成比赛要求的两类业务校验。

## 验证

第十一步见 docs/rfq-material-practice.md：选择模拟 RFQ 场景，对照买家材质要求与目录材质；普通邮件不套用规则。P01 来源为官方公开旧版，最新正文待核对，不能当作平台审核结论。报告参与生成并保存导出；本地对照不调用 AI。

第十步见 docs/payment-wording-practice.md：英文付款状态短语筛查，展示原句、风险标记与建议；示例检查不调用 API。新生成版本保存 C03 报告并导出；旧版本不补写。属于合同条款表达，不能当作第二个赛事类别。

第九步见 docs/check-report-practice.md：新生成版本独立保存 C02 校验输入/结果/依据，并发送给模型、导出 TXT/JSON。旧版不补写；页面显示所选版本生成时的报告。

第八步见 docs/payment-ratio-practice.md：第四步修改付款文字，即可观察两笔比例合计；明确错误停用生成，未知句式转人工核对。无需调用 AI。更新后的生成面板通过 ui_loader.py 检查文件指纹并按需重载，保留会话。

第七步操作见 docs/dialogue-practice.md；先理解、核对应用，再生成新版。理解和生成分别调用 API，其他交互不会自动调用。

第六步版本修订与下载练习见 docs/revision-practice.md。已成功的旧草稿可在同一会话迁移为 V1；下载后才能长期保存，刷新可能丢失会话历史。

第五步操作与接口限制见 docs/deepseek-setup.md。只在本地页面输入密钥，不发到聊天中。

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
& '.\.venv\Scripts\python.exe' -m pip check
```

检查有效 JSON、非正整数、布尔值/字符串数量、未知字段、空产品名、错误语言与损坏 JSON；模拟页面从成功到失败再恢复的交互。AppTest 是组件交互测试，不是浏览器像素级视觉测试。

2026-10-29：上述10个测试模块分别启动进程运行，共46项通过。单进程合并全部模块曾在Windows事件循环初始化停滞，采用上述逐模块命令；见 docs/verification/rfq-tests.log 与 docs/rfq-material-practice.md。

报价检查覆盖价格档位边界、最低订购数量、金额精度、异常价格表、切换商品与修改数量后的页面重算。第三步新增 6 项测试通过，原有 4 项测试通过；用户亲自操作验证另行记录。

第四步新增 3 项检查：空白/占位词保留提醒，填写不等于批准，填写→清空→待确认的页面反馈与原货品金额不受影响。原测试中全页警告数量断言改为检查对应输入警告，避免新增业务提示造成误报。全部 13 项检查通过；重启服务后实际浏览器验证填写与清空，截图见 docs/verification/step4-filled.png。

## 环境重建

### 开发更新后出现字段不存在

若更新 Python 文件后出现 `AttributeError: 'Product' object has no attribute 'moq'`，而新进程检查中该字段存在，可能是持续运行的服务仍加载旧模块。停止原服务（启动窗口 Ctrl+C），再双击 start-local.cmd，最后刷新页面。仅刷新页面或重新读取 JSON 不保证重新加载 Python 模块。修改商品 JSON 名称仍只需页面重新读取。

2026-10-27 实际排障：旧服务出现上述错误，重启服务后通过实际浏览器确认 300 个为 360.00 USD、改为 1000 个为 1,050.00 USD，错误消失。后续更新 Python 模型后需验证当前运行服务，不能仅依赖新进程 AppTest 和健康端点。

使用真实的 Python 3.12 解释器创建项目 `.venv`，然后安装项目根目录 `requirements-lock.txt`。当前机器 PATH 中的 python.exe 是 Windows 商店占位程序，不能用它证明 Python 已安装。当前环境的基础解释器来自 Codex 自带运行时，路径写在验证报告里。

```powershell
# 将下面路径替换为本机实际 Python 3.12 解释器
& 'C:\path\to\python.exe' -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip --isolated install --index-url https://pypi.org/simple -r requirements-lock.txt
```

虚拟环境不可直接复制给另一台机器，也依赖基础解释器路径；Codex 运行时若被移动或清理，需要重建。现场使用前应重新启动验证。依赖版本会在安装完成后固定；不要求 GPU 或 Docker。

## 数据与接口

- 服务器仅监听本机 127.0.0.1，关闭 Streamlit 使用统计。
- 第五步点击生成才发起真实模型调用；未输入密钥时不请求网络。没有用模板冒充模型响应。
- 数据仅在页面会话里保存，结果需主动下载。
- 已确认 DeepSeek 官方平台，端点与模型按官方文档配置；密钥仅放当前页面会话内存，不写源码或记忆文件。
- 本轮只安装 Streamlit、Pydantic 及它们的依赖；没有安装其他候选或记忆 MCP。

