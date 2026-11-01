# W3 集成与迁移说明

来源：本地 `origin/feature/w3-document-workflow` 快照 `8d6b9478d6ad4676ab191b06366b1daffd26fecd`。
本轮导入新增文件，手工整合共享文件；保留现有 W1/W2、动物台灯资料和 `gpt-6.1-sol` 默认配置。
由于 Git 元数据目录拒绝写入，本轮没有创建 Git merge commit，也没有推送；远程最新状态未能重新核验。

## 启动与使用

继续启动 `app.py`，使用已有账号登录，选择已有 Case，再选择“W3 交易单证工作台”。
管理员或单证岗位首次点击“启用 W3 单证工作台”；之后即可上传原件、补资料、处理核验问题、预览和生成交付文件。
页面不会要求注册另一套 W3 账号。创建新 Case、账号和分配仍使用 A 的侧栏入口。

## 数据与权限

- 默认同一个 `data/crossbot.db`：原 A/B 表保留；W3 使用追加式 `w3_snapshots` 和个人默认设置表。
- 当前 Case 与 W3 workspace 使用同一 ID。W3 保存时原子同步 Case、文档、复核任务、校验和审计投影，失败全部回滚。
- 不删除或替换原有 W1/W2 会话、生成记录和演示资料；同 Case 的重复原件被拒绝。
- 原件和生成文件存于数据库同级 `w3-files/`，按哈希保存；旧版本保留。
- `review.access.shared_w3_session` 每次按操作重验服务器会话、当前岗位和 Case 分配。
- 客服/产品可按既有权限上传资料；单证可维护单证和执行核验；复核可确认、核验和放行；管理员具备管理权限。
- 客服不能修改订单或生成交付单证；单证不能使用复核权限放行；角色和会话撤销即时影响后续操作。
- 原 `w3_app.py`、`storage.identity` 保留作为原分支兼容与测试入口；正式主入口不导入它们，不启用第二套账号或 `~/.crossbot/w3/workspace.sqlite3`。
- 此前独立 W3 运行留下的私有数据库没有自动导入或覆盖；如有旧数据，应另做有备份、可核对的迁移。

## 已接入功能与边界

原件版本/哈希、PDF 文本、Excel/Word 资料采集、来源定位、手填资料、人工确认、字段问题定位、来源预填、确定性规则、预览与多格式导出均已导入。
规则与人工确认是通过条件；预览不表示正式放行，不伪造证书和第三方签发文件。
W3 Case 不能借用 A 的通用状态接口跳过其专门核验流程。

当前虚拟环境是 Python 3.14.6。旧版 `rapidocr-onnxruntime` 只支持 Python <3.13，所以依赖使用条件标记；
图片/扫描 PDF 可保留原件并手工补录。本轮未验证真实 OCR，不应宣称已识图验收。

W1/W2 的附件选择和 V3 `AttachmentContent` 交接尚未跟随此迁移自动打通：
W3 原件/字段保存在 workspace 和共享 Document 投影中，不能自动冒充已审核的模型事实。
保留旧 `documents/__init__.py` 的轻量包入口，避免 W1/W2 导入附件授权服务时加载整个 W3 工作台。

## 验证与提交

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B -m unittest tests.test_integrated_w3 -q
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
git diff --check
```

本轮验证只使用临时库和合成资料，不读取或覆盖真实客户原件、密钥或生产账号。
2026-10-31 全量回归共运行 643 项：639 项通过、4 项跳过，失败与错误均为 0。
其中共享账号、Case 隔离、并发版本、事务回滚、重复原件和主页面入口集成测试 10 项全部通过。
Windows 测试已修复 SQLite 连接清理；创建符号链接缺少系统权限时仅跳过对应平台测试。
审核修改后手动提交；此次为内容集成，Git 历史尚未记录 W3 分支的合并祖先关系。
