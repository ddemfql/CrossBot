# 本地数据库

网页启动时自动创建 SQLite 数据库，默认位置为项目下 `data/crossbot.db`。
`STORAGE_DIR` 可以覆盖目录；相对路径始终相对项目根目录解析，避免从不同工作目录启动时创建多份库。

手动初始化并导入演示 Case：

```powershell
cd D:\hackathon\CrossBot
.\.venv\Scripts\python.exe -m storage --demo
.\.venv\Scripts\python.exe -m streamlit run app.py
```

重复运行初始化不会覆盖现有 Case。演示客户为虚构名称，产品和贸易资料来自
`docs/demo_case/case_fixture.json`，作为带演示标记的 Case 元数据保存。
没有生成假单据或已通过的复核/比对结果。

页面可新建 Case、选择 Case、导入演示案例并查看创建审计记录。
W1/W2 请求、反馈和输出按 Case 和工作流分开保存。刷新网页或重启服务后，
选择原 Case 即可查看历史。普通重跑不会重复调用模型。重复生成会追加历史。
两个页面同时更新同一对话时，过期保存会被拒绝，提示刷新。

## 数据表

| 表 | 用途 |
|---|---|
| cases | Case 基础信息与演示资料 |
| documents | 单据及版本 |
| review_tasks | 人工复核任务 |
| cross_checks | 交叉比对结果 |
| audit_events | 操作审计 |
| workflow_conversations | 按 Case 隔离的 W1/W2 对话与修订号 |

`PRAGMA user_version=2` 标记当前结构；首次打开旧版库时补建新表并保留数据。
当前新增的对话表有 Case 外键；原五张业务表的结构保持兼容。
数据库和运行资料在 `.gitignore` 中排除，提交代码后其他成员需在各自机器初始化。

## 业务模块接入

```python
from storage.bootstrap import open_store

store = open_store()
conversation = store.get_conversation(case_id, "sales")
conversation.messages.append({"role": "user", "content": "Customer inquiry"})
store.save_conversation(conversation)
```

`core.models.Case.metadata` 新增可选字段，旧对象读回时默认为空字典。
`core.schemas.WorkflowConversation` 是对话保存契约；`revision` 由存储层维护。
新建 Case 使用 `create_case()`，在同一事务内写入 Case 和创建审计。

V3 补充了 users、auth_sessions、case_assignments、resource_versions、
resource_publications、attachment_contents、search_reports、generation_artifacts 八张表。
登录和角色按钮已接入，知识管理及 W3 完整解析页面由 B/C 继续接入公共服务。
首次启动请通过网页或 `python -m auth admin` 创建管理员，再创建其他成员并分配 Case。
V3 权限服务接口详见 [B 接入说明](B_V3接入说明.md)。
原 `main.py` 模型生成器继续作为兼容调用入口，W1/W2 的模型参数和检索改进仍由 B 迁移。
