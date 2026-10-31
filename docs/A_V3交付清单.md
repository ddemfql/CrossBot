# A 的 V3 公共能力交付清单

状态：本地实现及验证完成，等待队长手动提交、推送。没有自动 commit/push。

## 交付内容

| 能力 | 文件 |
|---|---|
| 身份、角色、权限枚举 | core/identity.py |
| V3 模型与版本号 | core/v3.py、core/ports.py、core/schemas.py |
| 登录、会话、权限、Case 分配 | auth/service.py、auth/cases.py、auth/__main__.py |
| 登录与灰色角色按钮 | ui/auth_panel.py、ui/case_panel.py、app.py |
| 产品/知识/违禁词版本治理 | knowledge/governance.py |
| 受权文档版本、解析结果、人工确认 | documents/access.py |
| 搜索授权与来源记录 | providers/search_contract.py |
| API 原稿、人工稿、上下文与复核 | workflows/access.py |
| 数据表升级、事务、审计 | storage/sqlite_store.py、storage/v3_store.py |
| 数据库初始化、台灯样例导入 | storage/bootstrap.py、storage/__main__.py |
| 测试 | tests/test_v3_services.py、tests/test_database_integration.py |
| B 的完整接入说明 | docs/B_V3接入说明.md |

沿用并保留前一轮数据库接入改动，没有删除旧目录或更改 .env。
新增内容不依赖额外第三方包。

## 验证

- 42 项测试通过（包括原有测试与 Streamlit 页面测试）。
- 36 个 Python 文件通过语法解析。
- 本地 SQLite 升至 schema 2，共 14 张业务表，原 Case 保留。
- 升级前备份位于 data/backups，数据库及备份由 Git 忽略。
- 数据库 integrity_check=ok，foreign_key_check 无异常。
- 未运行真实模型、OCR 或搜索供应商；对应适配器仍由 B/C 实现。

## 手动提交建议

建议先检查差异，确认只包含本轮功能：

```powershell
git status --short
git diff --check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

建议提交信息：

```text
feat: add V3 authorization and versioned integration services
```

可按文件清单暂存：

```powershell
git add .gitignore README.md AI开发约束.md app.py core auth storage ui
git add documents/access.py knowledge/governance.py providers/search_contract.py workflows/access.py
git add tests/test_database_integration.py tests/test_v3_services.py
git add docs/database.md docs/contracts.md docs/directory-structure.md docs/B_V3接入说明.md docs/A_V3交付清单.md
git diff --cached --stat
git commit -m "feat: add V3 authorization and versioned integration services"
git push origin feature/foundation-contracts
git rev-parse --short HEAD
```

如果 status 中出现其他成员的新文件，不要照单全部暂存，应先核对归属。
推送完成后，把提交号和 B_V3接入说明.md 一起发给 B；也请同步 C/D。

## 兼容性和剩余分工

- SalesRequest / ListingRequest 原字段顺序保留，新增 context 可选字段；V3 服务要求 context。
- schema 2 是本地数据库版本，3.0.0 是公共契约版本。
- B 保留自己的业务页，合并 app.py 时逐项对接，不要整文件盲目覆盖。
- A 的服务属于进程内接口，没有宣称已提供 HTTP API。
- C 仍负责原文件存储、OCR/PDF/XLSX 解析及实际上传/下载。
- B 仍负责真实 API、搜索供应商、知识/产品管理业务页面。
- D 仍负责具体单证比对规则和全队端到端验收。
- 原始 SQLiteStore 是可信内部组件；业务入口不能以它绕过权限。
- 首次登录创建管理员后，由管理员创建岗位账号并分配 Case，无默认密码。
