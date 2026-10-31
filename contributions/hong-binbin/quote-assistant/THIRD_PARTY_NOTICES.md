# 第三方组件与服务声明

项目：询盘与报价小助手｜参赛者：洪彬斌｜整理日期：2026-10-30

本项目使用第三方Python组件构建界面与数据校验，通过外部模型API完成理解和文本生成。下面按当前锁定环境和本地安装元数据列出来源，不将开源依赖、外部服务或公开参考资料描述为本项目原创。

## 直接依赖与运行时

| 项目 | 实际版本 | 用途 | 许可来源 |
|---|---|---|---|
| Streamlit | 1.66.1 | 本机网页、控件及开发检查 | 安装元数据 `License-Expression: Apache-2.0`；[官方仓库许可](https://github.com/streamlit/streamlit/blob/develop/LICENSE) |
| Pydantic | 2.14.1 | 输入、商品及模型返回结构校验 | 安装元数据MIT；[随包许可原件](docs/third-party/licenses/pydantic-2.14.1/licenses/LICENSE) |
| CPython | 3.12.14 | Python运行时与标准库 | [当前运行时附带许可](docs/third-party/licenses/cpython-3.12.14/LICENSE.txt)，包含其历史及其他声明 |

项目通过Python标准库urllib调用模型，未安装DeepSeek专用SDK。`requirements.in`声明两个直接依赖；`requirements-lock.txt`记录39个环境组件。锁定环境中的包不意味着每个包都在每次演示中被实际调用。

## 完整环境清单与许可原件

- [39项组件及许可元数据表](docs/third-party/dependency-inventory.md)。
- [完整元数据与文件校验清单](docs/third-party/dependency-inventory.json)：包含精确版本、上游项目链接、依赖声明、许可分类、原件路径和SHA256。
- `docs/third-party/licenses/`：从安装分发包逐字节复制的78份LICENSE／COPYING／NOTICE等文件，另有1份CPython运行时许可；版权声明与上游内容均保留。

39项版本均与锁定文件一致。NumPy、pandas等包可能包含多份附属组件声明，应查看全部随包原件，不能只按表格中的单一许可证名称概括。`License-Expression`、旧式`License`或分类字段原样记录；例如python-dateutil标为Dual License时不擅自替上游选择许可。

**已知范围限制：**Streamlit本机分发包的文件清单没有附带许可原件；其元数据标为Apache-2.0，官方develop分支许可也可读取，但尚未取得与安装版1.66.1逐一匹配的完整许可／捆绑声明包。本轮尝试通过GitHub API固定许可提交来源时遇到403限流，未生成或伪造该原件副本。此清单是Python锁定环境及随包声明快照，不是完整的JavaScript／原生二进制组件SBOM。

当前交付方式为项目源码与依赖锁定文件，由接收者安装环境；未制作包含`.venv`或解释器的独立二进制分发包。若后续改为捆绑分发，应针对实际打包内容补齐上述缺口并保留对应原件。

## 外部模型服务与开发辅助

| 服务／工具 | 使用方式 | 与本作品的关系 |
|---|---|---|
| DeepSeek官方API | 当前代码固定`https://api.deepseek.com/chat/completions`和`deepseek-flash`；由用户提供账户密钥，按按钮调用 | 外部托管服务，未下载或再分发模型权重；不能把API服务当作本项目开源组件或推断适用模型权重许可 |
| Codex | 辅助编写、调试代码和整理文档；洪彬斌运行并验证 | 开发辅助工具，不是应用运行依赖；当前应用不需要调用Codex |

模型服务的账户使用条件由实际服务协议约束，本声明不替代协议、不承诺服务可用性或输出权利。本次未读取密钥、未代用户调用模型。未安装先前调研中的Dify、MarkItDown、Promptfoo或第三方记忆MCP。

## 公共资料与项目内容

公开贸易规则、联合国背景资料和赛事截图的来源、读取范围与边界见 [数据来源与知识依据](docs/data-sources.md)。这些资料不因被引用而获得本项目授予的开源许可，也不表示相应机构认可本作品。

项目专用界面、报价计算、规则组合、对话确认与版本导出流程由本项目在AI辅助下实现；模拟业务资料为项目测试设定。项目尚未选择对自身源码授予开源许可证，第三方许可不自动成为本项目许可。本声明不声称全部底层代码由参赛者独立手写。

## 复核与更新

在项目根目录执行以下命令可从当前环境重建组件清单及许可副本，不访问网络，也不调用模型：

```powershell
& '.\.venv\Scripts\python.exe' tools/export_dependency_notices.py
```

脚本要求安装版本与锁定文件一致；不匹配时停止。它不会删除旧版本目录；交付时应按当前清单取文件。新增或升级依赖后重新生成并复核，实际文件以SHA256清单对应版本为准。核对结果见 [本轮检查记录](docs/verification/data-and-notices-review.md)。
