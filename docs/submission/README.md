# hackathon-final 提交说明

交付形式为主分支 `main` 的完整 Git 工程及该分支上的 `hackathon-final` Tag。GitHub 的 Tag 源码 ZIP 与本地打包工具都包含该提交下全部受跟踪文件，包括教程、截图、用户授权的原始示例包和归档贡献项目；不打包工作区未提交内容、`.git`、`.venv`、数据库或密钥。

| 要求 | 交付位置 |
| --- | --- |
| 可编译运行的完整工程包 | Tag `hackathon-final` 的源码 ZIP；或用下方命令生成相同提交的工程包 |
| README | [项目 README](../../README.md)：环境、安装、账号、模型配置、运行、测试与限制 |
| 平台配置导出 | [platform-config.json](platform-config.json)：主入口、ZIP 入口、存储、模型默认值及空密钥；[streamlit-config.toml](streamlit-config.toml)：可选启动模板 |
| 第三方组件声明 | [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) 和 [依赖清单](third-party-components.json) |
| 测试教程与示例 | [W1/W2/W3 教程](../../documents/tester_guide/README.md)；原 ZIP 见其 examples 目录 |

## 取得并运行最终版本

```bash
git clone --branch hackathon-final --single-branch https://github.com/ddemfql/CrossBot.git
cd CrossBot
```

Tag 检出为 detached HEAD，适用于复现最终作品；需要继续开发时另建分支。也可从 [GitHub 下载该 Tag 的完整源码 ZIP](https://github.com/ddemfql/CrossBot/archive/refs/tags/hackathon-final.zip)，解压后照项目 README 第 1～6 节安装 Python 3.12、依赖并启动 `app.py`。私有仓库需有读取权限。

本项目是 Python 工程，无单独的前端编译步骤。通过 `python -m pip install -r requirements.txt` 准备依赖，用 `python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502` 启动。此处 `python` 指按根 README 创建的虚拟环境解释器。

`platform-config.json` 是供评审查看与手工复现的配置导出，不是自动导入功能。模型 Key 为空、数据发送确认关闭，真实生成前必须在网页填写；账号在首次启动时创建。`streamlit-config.toml` 可复制到项目根 `.streamlit/config.toml`；不复制也能按 README 命令启动。W3 ZIP 测试使用独立端口和账号，不与主站共享数据。

## 本地生成完整工程包

在带 `.git` 的仓库根目录执行；脚本仅需 Python 标准库与 Git，无需先安装业务依赖：

```powershell
# Windows
.\.venv\Scripts\python.exe docs/submission/package.py
```

```bash
# Ubuntu / WSL
./.venv/bin/python docs/submission/package.py
```

输出 `dist/CrossBot-hackathon-final.zip` 与 `dist/CrossBot-hackathon-final.manifest.json`。清单包含 Tag、精确提交、Git tree、文件数量、ZIP 字节数和 SHA-256。工程包从 Tag 的 Git tree 生成，不依赖本机业务库；GitHub 下载的源码 ZIP 与本地包可能有不同的压缩元数据，内容以提交和文件为准。下载的源码 ZIP 不含 `.git`，可直接运行应用；若要重新执行打包脚本，先 clone 仓库。

示例包是用户授权的真实交易资料，未脱敏，随工程一并交付；只用于指定测试范围。工程未附 Python 运行时、依赖二进制、云服务额度、真实模型 Key、管理员账号或业务数据库。W1/W2 的真实 API 可用性与交易正文正确性仍需人工验收。
