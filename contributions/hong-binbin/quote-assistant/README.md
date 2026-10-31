# 洪彬斌贡献：询盘与报价修订助手

这是向CrossBot提供的独立运行原型，可作为W1询盘流程的报价计算、付款检查和版本修订参考。贡献者：洪彬斌，代码在AI辅助下完成并由本人运行验证。尚未接入仓库根目录主界面。

## 运行

先进入本目录（包含本README与start-local.cmd），使用Python 3.12创建独立环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m streamlit run local_lab/app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

浏览器打开 http://127.0.0.1:8502/ 。端口8502便于与CrossBot根应用并行；start-local.cmd仍使用8501。已配置环境也可直接运行；本目录不含虚拟环境和密钥。

模型使用DeepSeek官方API，端点和模型名在local_lab/deepseek_client.py中；在页面第二栏自行输入密钥。模型调用会消耗账户额度；本模块没有自动Mock降级，缺少密钥仍可查价计算和本地检查。不要复用根应用.env的假定配置。

## 已实现的流程

- 首次报价：模拟商品目录 → Decimal按阶梯价计算 → 付款完整性/比例、RFQ材质检查 → 生成英文客户草稿与中文内部清单 → 人工复核和下载。
- 对话修订：AI理解数量或付款修改 → 代码重算并预览差异 → 人工应用 → 另行生成新版 → 记录原始要求、结果及版本差异。
- 可复用纯逻辑：local_lab/quotation.py、payment_ratio.py、rfq_check.py、revisions.py。移植时同时检查各模块import及目录资料依赖。

## 测试与演示记录

12个独立检查模块的命令见local_lab/README.md；均可离线运行，模型响应被mock。迁入团队目录后的实际结果见TEAM-VERIFICATION.md，不把原目录检查冒充为本目录结果。

模拟商品BAG-001：500个×1.20美元=600美元；改1000个后1.05美元/个、货品金额1050美元；再将拟议付款50/50改为30/70。用户实际模型调用导出在docs/verification/demo-two-rounds-history.json；两条对话齐全。所有商品/买家要求都是模拟资料，既有真实调用不等于真实客户验证。

两个固定恢复按钮依赖docs/verification/rfq-live-history.json和demo-round1-history.json原字节。最新V3仅供查看，尚不支持任意JSON导入；历史仍需用户下载备份。

## 与团队应用整合时的边界

金额仅含货品，不含运费/税费/定制费；付款条款均需双方确认。C02/C03同属合同相关检查；RFQ材质规则只作公开旧规则的有限参考，不能代表平台审批。当前没有向量检索RAG、目标国全面合规审核或自动发送功能。

本次只增加此独立目录。根app.py、main.py、模型封装、依赖和现有标签由团队继续维护。旧文档含单人阶段及原机路径，作为模块开发资料保留；单人海报和讲稿未随本次贡献上传，不作为团队最终交付。

组件及服务声明见THIRD_PARTY_NOTICES.md，许可副本见docs/third-party/。尚未对自有代码指定开源许可证；与团队后续许可选择另行协调。本模块报价/修订结果及模拟资料不作交易承诺。
