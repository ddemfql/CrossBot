# CrossBot 外贸协作工作台

主入口为 `app.py`。工作台提供真实询盘回复、营销文案与交易单证核对，使用登录角色权限和本地 SQLite。
当前工作方式与模拟版本恢复说明见 [真实工作流](docs/real-workflow.md)。

## 功能

- W1：直接录入客户询盘，按已发布产品与知识关键词检索，调用配置的真实 API 回复并追问缺少的信息。支持多轮客户消息、调整回复、历史保存与来源查看。明确下单后自动生成一个待复核订单 Case。
- W2：按选定平台与语言生成营销文案，使用已发布资料、官方平台规则摘要和风险词库；生成结果仍需人工复核，不能代表平台审核通过。
- W3：选择订单 Case，查看 W1 交接草稿、上传平台订单与其他原件、解析与人工复核，再用 Python 规则交叉比对。缺失或未确认信息不得自动放行。
- 资料与知识：产品资料、知识库、风险词库分为三个独立子页；管理员和获授权岗位上传、提交审核、由另一名审核人员审核后发布。
- 账号与岗位：按当前激活角色核权，Case 对其他岗位的可见性由管理员分配。询盘创建前按所属账号隔离。
- 模型设置：按账号保存设置；可选择在本机用 Windows DPAPI 加密保存 API Key。业务发送仍需明确确认。

模拟工作区和断网保底入口已从主界面移除。旧模拟适配器、合成样例和单独组件测试保留用于开发回归，不能作为真实调用结果。

## 启动

Windows PowerShell：

```powershell
cd D:\hackathon\CrossBot
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

首次打开空数据库时创建管理员。已有数据库继续使用原账号，刷新后恢复仍在有效期内的登录会话。
默认数据库是 `data/crossbot.db`。API Key 在模型服务密码框填写；默认地址为 TokenHubCloud，实际模型名称与权限以服务商账号为准。
API 请求只会在用户生成时发送，不因打开页面、刷新或切换岗位自动调用。失败不会伪装成模拟成功。

## 测试

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

自动测试使用合成数据库与模型替身，不验证真实账号的额度或模型权限。
PDF/Office/图片的自动解析能力取决于已安装后端；文件原件保存与解析成功是不同状态，不能因文件上传成功就当作字段已确认。
模型可能错误理解语义，保留原文引证与人工复核；确定性规则只能核验其覆盖字段。
目前不连接外部电商订单系统、不向平台自动发送消息或提交订单，也没有接通真实外部搜索供应商。

## 开发

[AI 开发约束](AI开发约束.md) · [目录与协作](docs/directory-structure.md) · [公共契约](core/README.md) · [V3 服务](docs/B_V3接入说明.md)

历史阶段文档记录当时状态；当前主入口行为以本 README、真实工作流说明和代码测试为准。
数据库、密钥、原始客户文件与本机备份不提交到 Git。
