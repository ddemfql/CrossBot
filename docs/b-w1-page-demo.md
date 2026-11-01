# B第二步：W1页面与两轮修改（模拟验证）

2026-10-31｜feature/w1-w2-platform。使用core的SalesRequest、GenerationResponse、RiskItem及错误类型，无公共契约变更，无新增依赖。

## 修改及行为

- app.py路由W1至workflows/sales_ui.py，移除旧页面按行拆W1字段路径。W2保留旧流程，延迟加载其依赖；旧main.py不改动。
- sales_ui：命名表单、首版/修改按钮、V1/V2/V3、原输入JSON核对。改动表单后须重新生成开启会话，失败保留原成功历史。
- conversation：明确initial/revision；修改传原始业务资料、上一版内容及反馈，成功才追加。页面重跑不自动调用。
- sales扩展显式反馈参数；sales_review每次重新筛查CE certified、FDA approved、100% safe。未命中也需人工，不是完整合规或平台审核。
- providers/mock_client回显资料和反馈，明确模拟，无翻译、语义改写、真实模型或网络。
- 新增会话5项、页面3项测试；原19项仍通过。

## 验证

团队根运行 `../../.venv/Scripts/python.exe -B -m unittest discover -s tests -v` 共27项通过。覆盖多行输入、两轮修改、失败保留版本、重跑不重复、输入变更隔离及W2入口。AppTest是组件测试，不等于真实浏览器/模型验证；W2测试使用替身，不调用旧API或读取.env。

py_compile通过，git diff --check通过；app.py的Windows CRLF导致过空白检查报错，已统一UTF-8/LF及清理行尾空白。当前本地服务8502健康ok，8501未操作。

## 启动和用户验收

```powershell
cd 'F:\INTERCITY HACK\team\CrossBot-B'
& '..\..\.venv\Scripts\python.exe' -B -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

打开http://127.0.0.1:8502/，选择W1。填多行询盘/产品，选德文，生成首版V1；第一次修改「请更礼貌，保留参数」得V2，再输入「请追加确认包装的询问」得V3。展开输入核对字段，三版均应标注模拟且保留各轮反馈。空白修改应报错，历史不丢。

## 限制与交接

模拟只记录要求，不会将「数量改1000」解析应用到原SalesRequest。真实API、语言生成质量、知识检索及完整规则待后续。同步响应使用公共GenerationResponse，流式事件尚未接入，后续使用core.events，不另建特殊字符串标记。

历史仅在会话内存，无磁盘持久化或下载。本轮未commit/push/创建PR；只在B工作区修改，A/C/D业务模块未改。无需同步新公共契约。第一步说明的“未接入旧UI”是当时状态，本步已接W1新面板；W2旧缺陷仍待处理。
