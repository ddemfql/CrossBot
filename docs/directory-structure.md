# CrossBot 新目录结构

```text
CrossBot/
├── app.py                         # Streamlit 应用入口，后续只保留页面编排
├── core/                          # A：公共契约和跨模块基础类型
│   ├── models.py                  # Case、Document、Review、CrossCheck 等模型
│   ├── schemas.py                 # W1/W2 请求响应结构
│   ├── events.py                  # 流式和工作流事件
│   ├── errors.py                  # 统一错误码和异常
│   └── config.py                  # 环境变量和运行配置
├── auth/                          # A：登录、服务端角色核权和 Case 分配
├── ui/                            # 公共登录/Case 页面编排
├── workflows/                     # B：W1/W2 业务工作流
├── providers/                     # B：OpenAI 兼容接口和 Mock 适配
├── knowledge/                     # B：结构化知识库和检索
├── case_workflow/                 # C：W3 Case、岗位任务和状态流转
├── documents/                     # C：文件上传、PDF/Excel 解析、字段映射
├── review/                        # C：人工复核服务和界面
├── storage/                       # 公共持久化适配，第一阶段可使用 SQLite
├── validation/                    # D：字段规范化和跨单据硬规则比对
├── tests/                         # 自动化测试和脱敏样例
│   └── fixtures/
├── knowledge_base/                # 旧 TXT 知识库，迁移完成前保留
├── prompts/                       # 旧版提示词，迁移完成前保留
├── utils/                         # 旧版工具，迁移完成前保留
├── docs/                          # 接口、流程和业务规则文档
├── requirements.txt
├── .env.example
└── README.md
```

## 分支对应关系

| 分支 | 目录 | 负责人 |
|---|---|---|
| `feature/foundation-contracts` | `core/`、`storage/` 基础接口 | A |
| `feature/w1-w2-platform` | `workflows/`、`providers/`、`knowledge/` | B |
| `feature/w3-document-workflow` | `case_workflow/`、`documents/`、`review/` | C |
| `feature/cross-check-quality` | `validation/`、`tests/` | D |

## 依赖方向

```text
app.py
  -> workflows / case_workflow / review
  -> core

workflows
  -> providers / knowledge / core

case_workflow
  -> documents / review / storage / core

validation
  -> core

documents
  -> core / storage

review
  -> core / storage
```

业务模块通过 `core` 的模型、事件和错误类型通信。页面层不应直接拼接换行字符串，也不应把解析或交叉比对规则写进 `app.py`。

## 其他成员拉取方式

公共结构提交并推送后，其他成员执行：

```powershell
git fetch origin
git switch feature/foundation-contracts
git pull --ff-only origin feature/foundation-contracts
```

基于新结构创建自己的开发分支：

```powershell
git switch -c feature/w1-w2-platform origin/feature/foundation-contracts
git switch -c feature/w3-document-workflow origin/feature/foundation-contracts
git switch -c feature/cross-check-quality origin/feature/foundation-contracts
```

如果本地已有同名分支，先执行：

```powershell
git fetch origin
git rebase origin/feature/foundation-contracts
```

公共模型发生变化时，先同步 A 的分支，再继续自己的模块开发。

V3 公共服务详见 [B 接入说明](B_V3接入说明.md)。A 新增的 `workflows/access.py`、
`documents/access.py`、`providers/search_contract.py`、`knowledge/governance.py` 是
各业务分支共用的授权/存储门禁，不包含真实模型、解析器或搜索供应商实现。
业务扩展优先调用这些服务，不另建角色模型或跳过版本审核。
