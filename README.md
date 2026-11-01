# CrossBot 外贸协作工作台

CrossBot 使用 Streamlit 提供询盘回复（W1）、多平台营销文案（W2）和交易单证核对（W3），以本地 SQLite 保存账号、业务记录、版本和审计。**正式启动入口是 `app.py`。**

本文覆盖新电脑从拉取仓库到首次使用的配置。仓库不包含 Python、虚拟环境、API 账号/额度、管理员账号和本机数据库，需要按下文安装或创建。另附用户授权的交易测试 ZIP，见第 6 节；它不会自动导入业务库。启动、登录和本地单证处理无需模型 Key；W1/W2 真实生成需要可用的外部 API。

第一次使用按「准备环境 → 安装启动 → 创建账号 → 配置资料与模型 → 运行测试教程」操作。已有环境可直接打开 [W1/W2/W3 测试速查](documents/tester_guide/README.md)；下载仓库后双击 [交互测试教程](documents/tester_guide/index.html) 可离线查看步骤、截图和验收清单。

| 工作 | 入口 | 使用前提与结果 |
| --- | --- | --- |
| W1 询盘回复 | 主工作台 → 起草客户回复 | 输入询盘、产品依据、语言等，配置真实 API；得到待人工复核的回复，明确下单可交接为订单草稿 |
| W2 多平台文案 | 主工作台 → 多平台文案 | 选择 Case、产品描述、平台和语言，配置真实 API；得到所选平台的文案与风险提示 |
| W3 单证核对 | 主工作台 → 交易单证工作台 | 选择 Case 并启用 W3，逐份上传、采集、人工确认、规则核验，生成并下载单证 |
| W3 示例 ZIP 整包测试 | 独立 ZIP 工作台，端口 8503 | 按第 6 节另行启动；使用独立账号与数据目录，整包导入后生成 CI/PL |

## 1. 仓库之外需要准备什么

| 项目 | 准备方法 | 是否必需 |
| --- | --- | --- |
| Git | 从 [Git 官网](https://git-scm.com/downloads/) 安装，用 `git --version` 检查 | 拉取与更新必需 |
| 64 位 Python 3.12 | Windows 从 [Python 官网](https://www.python.org/downloads/) 获取，安装时启用 Python Launcher 并加入 PATH；Ubuntu 按下文用 uv 安装 | 必需 |
| 虚拟环境和 Python 依赖 | 按第 2 节创建 `.venv`，安装根目录 `requirements.txt` | 必需，不能复制他人电脑的虚拟环境 |
| 浏览器 | 能访问本机 `127.0.0.1:8502` 的现代浏览器 | 必需 |
| 网络 | 安装时访问 GitHub、PyPI 及包下载站；生成时访问所选 HTTPS 模型服务 | 安装及真实生成必需 |
| 模型服务账号 | 自行开通支持 Responses 或 Chat Completions 的服务，取得基础地址、Key、模型 ID 和额度 | W1/W2 真实生成必需 |
| 可写数据目录 | 默认项目下的 `data/`，首次启动自动创建，可用 `STORAGE_DIR` 修改 | 必需 |
| 中文字体 | Windows 通常已有宋体；独立 Ubuntu 可安装 `fonts-arphic-uming`，见第 5 节 | 中文 PDF 导出建议配置 |

无需单独安装 MySQL、PostgreSQL、Redis、Node.js、Docker、CUDA、Microsoft Office、Tesseract 或 Poppler。SQLite 来自 Python 标准库，PDF 渲染和 OCR 后端随 Python 依赖安装，OCR 使用 CPU。

本文统一使用 **Python 3.12**。应用部分功能虽可在 3.10 运行，但完整测试使用了较新版 Python 的接口，3.10 会在导入测试时失败；Python 3.13 及以上又会跳过 requirements 中的 RapidOCR，因此不要直接使用系统默认版本或最新大版本代替 3.12。

## 2. 拉取、安装、启动

以下命令针对当前工作基线 `feature/foundation-contracts`，明确指定分支，避免拉到内容不同的默认分支。逐条执行，成功后再继续；项目以后合入其他分支时，替换为团队公布的分支名。私有仓库需先取得读取权限，按 [GitHub 认证说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github) 使用凭证管理器或个人访问令牌：Windows 可按凭证管理器提示在浏览器登录，Ubuntu HTTPS 密码提示应填写获授权的令牌，不能填写网页登录密码，也不要将令牌写进 clone URL。

### Windows PowerShell

打开 PowerShell，进入希望保存项目的本地目录，再执行：

```powershell
git clone --branch feature/foundation-contracts --single-branch https://github.com/ddemfql/CrossBot.git
cd CrossBot
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

浏览器打开 **[http://127.0.0.1:8502](http://127.0.0.1:8502)**。保持终端运行，停止时按 `Ctrl+C`。

没有 `py` 但已安装正确版本 Python 时，用 `python --version` 确认是 3.12 后，将两条 `py -3.12` 改成 `python`。本文直接调用虚拟环境解释器，无需运行 `Activate.ps1`，无需修改 PowerShell 执行策略。

### Ubuntu 22.04 / WSL2 Ubuntu 22.04

Windows 首次使用本路线时，按 [微软 WSL 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)，以管理员 PowerShell 执行 `wsl --install -d Ubuntu-22.04`，按提示重启并创建 Ubuntu 用户。已有发行版则直接进入 Ubuntu。以下命令全部在 **Ubuntu 终端**执行：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip libgl1 libglib2.0-0 libgomp1 fonts-arphic-uming
python3 -m pip install --user --upgrade uv
python3 -m uv python install 3.12
mkdir -p ~/projects
cd ~/projects
git clone --branch feature/foundation-contracts --single-branch https://github.com/ddemfql/CrossBot.git
cd CrossBot
python3 -m uv venv --python 3.12 --seed .venv
./.venv/bin/python --version
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pip check
./.venv/bin/python -B -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

仍在 Windows 浏览器打开 **[http://127.0.0.1:8502](http://127.0.0.1:8502)**。这里用 [uv 安装 Python](https://docs.astral.sh/uv/guides/install-python/) 并创建带 pip 的 3.12 虚拟环境，不替换 Ubuntu 系统 Python；下载 Python 还需要访问 GitHub release 下载站。uv 仅用于环境准备，不是业务运行依赖。WSL 项目建议放在 `~/projects` 并使用 Linux 虚拟环境；Windows 原生安装使用本地磁盘和 Windows 虚拟环境，两者不能混用。

其他 Linux 发行版和 macOS 需调整系统包安装方式，本文未验证这些平台。

## 3. 首次登录与业务初始化

1. 首次打开空数据库会显示创建管理员表单，自行设置用户名和**至少 12 位**密码，没有通用账号或默认密码。
2. 管理员在“账号与岗位”创建成员并分配角色。权限取决于**当前激活角色**；非管理员还需分配 Case 才能访问对应业务。管理员可执行管理和业务操作。
3. 到“产品资料”“知识库”“风险词库”，按页面模板上传有依据的业务资料，保存草稿并提交审核。由**另一名有审核权限的账号**审核，再由管理员发布。提交人不能自审，包括管理员；首次正式发布通常至少需要管理员和独立复核员两个账号。
4. 按第 4 节配置模型后，进入“起草客户回复”新建询盘并生成，W1 无需预先创建 Case。空库没有已发布产品，未提供的规格、价格、交期等保持待确认。
5. W2 先在“我的 Case”（部分提示称“Case 协作”）创建或选择 Case，再到“多平台文案”填写中文产品描述（含准确 SKU 或可识别产品信息）、选择平台和语言，可填目标市场。程序按描述检索已发布产品。非管理员需取得运营岗位及 Case 分配；没有建单权限时由管理员或有权限岗位先建单并分配。
6. W3 选择 Case，进入“交易单证工作台”，首次由管理员或单证岗位按提示“启用 W3 单证工作台”。上传原件、核对分类、采集字段、人工补充与确认，再执行规则核验。W1 明确下单建立的 Case 仍是待复核草稿。

数据库与表自动创建，正常使用**无需手工建表，也无需执行 `python -m storage --demo`**。也可在创建网页账号前通过终端创建首个管理员：

```powershell
.\.venv\Scripts\python.exe -m auth admin
```

按隐藏输入提示设置并确认密码；Ubuntu 对应 `./.venv/bin/python -m auth admin`。`admin` 是自行选择的用户名。已有账号的数据库不能再次初始化，直接用原账号登录。

拉取仓库不会还原其他电脑的账号、已发布产品、订单历史或业务数据库。随附交易 ZIP 仅作为用户指定的测试输入，合成样例与交易原件都不能代替本次业务的认证核实或价格确认。

## 4. 模型 API 配置

当前 `app.py` 在“模型服务”使用会话内配置，**不会从 `.env` 或环境变量自动填入模型 Key**。主入口无需复制 `.env.example`；该文件与 `utils/` 的 dotenv 读取属于旧入口兼容路径。

登录后打开“模型服务”：

| 页面字段 | 填写方法 |
| --- | --- |
| 服务基础地址 | 服务商提供的 HTTPS API 地址，不填控制台、密钥管理页、登录页。预填 `https://api.tokenhubcloud.net`，不是项目自带免费服务 |
| 接口协议 | 按供应商说明选择 Responses 或 Chat Completions。“自动”对代码识别的服务选择 Responses，其他服务默认 Chat Completions |
| 模型名称 | 控制台实际授权的模型 ID。页面预填值不代表该账号有权限或额度 |
| API Key | 在密码框输入有效 Key，不写进 README、源码或业务资料 |
| 发送确认 | 勾选确认将业务资料及历史草稿发送到所选服务并使用账户额度，再返回业务页生成 |

默认 TokenHubCloud 根地址会解析为 `https://api.tokenhubcloud.net/v1/responses`。其他兼容服务若要求 `/v1` 前缀，应填写 `https://你的服务域名/v1`，并选择匹配协议。页面显示最终接口地址，请核对它；客户端只接受 HTTPS，不接受 HTTP 本地服务地址。

配置在当前页面会话内保存，切换功能页保留；刷新、退出或重建会话后重新填写 Key 并确认。修改服务地址会清除 Key。打开页面或切换岗位不会自动发起模型请求，点击业务生成才调用，等待 60 秒，不自动重试，失败不伪装为模拟成功。主界面没有单独“测试连接”按钮，可用一条无敏感资料的询盘验证。

### 环境变量与作用范围

默认无需设置环境变量。主入口实际使用的可选项为 `STORAGE_DIR`，在启动前设置：

```powershell
# Windows：替换为自己的可写目录
$env:STORAGE_DIR = "D:\CrossBotData"
```

```bash
# Ubuntu / WSL
export STORAGE_DIR="$HOME/.local/share/crossbot"
```

相对路径以项目根目录解析，`~` 会展开。变量仅对当前终端及子进程生效，新终端需重新设置。切换目录等于切换数据库，不会自动迁移旧账号和资料。

以下为 `core.config.load_config()` 支持的全部配置变量，供公共库/旧入口开发查询，**不能代替主界面的模型设置**：

| 变量 | 公共配置默认值 | 当前主入口的行为 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 无 | 页面 Key 不从此处读取 |
| `OPENAI_BASE_URL` | 无 | 页面地址独立设置 |
| `MODEL_NAME` | `gpt-3.5-turbo` | 页面模型独立设置 |
| `MODEL_API_PROTOCOL` | `auto` | 页面协议独立设置；公共配置另支持 `responses`、`chat_completions` |
| `MOCK_MODE` | 无 Key 时为真，有 Key 时为假 | 不能用它把主界面切换成模拟模式 |
| `REQUEST_TIMEOUT_SECONDS` | `60` | 页面真实客户端固定使用 60 秒 |
| `MAX_UPLOAD_SIZE_MB` | `20` | W3 另有固定 20 MiB 限制，调大变量不能解除 |
| `STORAGE_DIR` | `data` | 主入口数据库及关联文件目录 |

独立开发入口 `w3_app.py` 和归档导入脚本另支持 `CROSSBOT_W3_DATA`，默认 `~/.crossbot/w3`，与主工作台数据及账号不同。正常使用 `app.py` 无需设置此变量。

## 5. 解析、OCR 与中文 PDF

根目录 `requirements.txt` 已包含文本 PDF（pypdf）、XLSX（openpyxl）、XLS（xlrd）、DOCX（python-docx）、PDF 导出（reportlab）、PDF 图像渲染（pypdfium2）等依赖。Python 3.10～3.12 会安装 RapidOCR、ONNX CPU runtime 及传递依赖，无需云 OCR Key，OCR 模型随包提供。

| 功能 | 环境与限制 |
| --- | --- |
| 文本 PDF、XLS/XLSX、DOCX、TXT/CSV | 本地解析，字段采集受模板和布局限制，不支持任意格式任意版式 |
| 图片与扫描 PDF | 使用本地 RapidOCR；Linux 的原生运行库见第 2 节 |
| Windows OCR 备用后端 | RapidOCR 无法导入时尝试，需 Windows 自带 PowerShell 和已安装的 OCR 语言能力；WSL/Linux 无此回退 |
| XLSX/PDF 导出 | Python 库和内置布局生成，可上传自己的 XLSX 模板，无需 Office 或 LibreOffice |
| 中文 PDF 字体 | 依次尝试 Windows 宋体、WSL Windows 宋体、Ubuntu UMing；没有可嵌入字体时使用 STSong-Light，由阅读器解析字形 |

字体检测路径为 `C:/Windows/Fonts/simsun.ttc`、`/mnt/c/Windows/Fonts/simsun.ttc`、`/usr/share/fonts/truetype/arphic/uming.ttc`。独立 Ubuntu 中文显示异常时，运行 `sudo apt install fonts-arphic-uming`，重启应用重新导出；不要将受授权限制的字体提交到仓库。

Windows 备用 OCR 需通过系统语言设置安装所需语言及其 OCR 能力。管理员 PowerShell 可执行 `Get-WindowsCapability -Online -Name Language.OCR*` 查看已安装能力；按本文安装 RapidOCR 后通常无需使用此备用方案。

W3 单文件上限 **20 MiB**，即使 Streamlit 上传提示更大也不改变服务限制。扫描 PDF OCR 最多 100 页，并受像素和文本长度限制；加密、损坏、超限文件可能解析失败。主工作台不接收 ZIP：可先本机解压再逐份上传；若按测试教程整包导入，使用第 6 节的独立 ZIP 工作台。

上传成功只代表原件保存，不代表字段已确认。无 OCR 或复杂布局无法识别时，可保存/下载原件并按可信依据人工录入。Case 通过需要人工确认与 Python 确定性规则共同满足，AI 不能批准单证。

## 6. 自检与验收

在项目根目录执行，Ubuntu/WSL 将 `.\.venv\Scripts\python.exe` 换为 `./.venv/bin/python`：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import streamlit, openai, dotenv, pypdf, openpyxl, xlrd, xlwt, docx, reportlab, pypdfium2; from rapidocr_onnxruntime import RapidOCR; RapidOCR(); print('Dependencies and OCR OK')"
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

自动测试使用合成数据和模型替身，无需真实 Key，不验证真实 API 额度或模型权限。依赖本地真实交易资料的专项测试未提供资料时可能跳过，不需要取得客户 ZIP 才能运行常规测试。

此前环境验证（2026-11-01，代码基准 `4025e92`）：从该 Git 提交的受跟踪文件导出独立副本，未复制本机数据库或虚拟环境，在 WSL Ubuntu 22.04 / Python 3.12.16 中重新安装依赖。`pip check`、依赖导入、RapidOCR 实例初始化、Python 语法检查、`app.py` 空库创建管理员与登录、Streamlit HTTP 健康检查均通过；完整测试运行 745 项，结果为 `OK (skipped=3)`。Windows 命令块已检查 PowerShell 语法，未在全新 Windows 系统实装验证；真实 API 账号、额度和远程 GitHub 认证不属于该本地验证结果。

安装验收：

- `pip check` 无冲突，依赖导入和 OCR 初始化成功。
- 浏览器可打开登录页，首次可创建管理员，重启后仍能登录。
- 可创建 Case、启用 W3，并保存、查看/下载非敏感测试原件。
- 发布资料并配置有效 API 后，W1/W2 可生成并保存真实回复；外部账户额度和服务网络需可用。
- 导出 XLSX 可打开、PDF 中文可读，字段来源与人工确认状态符合预期。

本次文档整合另运行 W3 上传/ZIP 导入、集成、严格复核与生成界面回归，37 项通过；教程入口语法与隔离空库启动、18 个本地文档链接、图片引用及 JavaScript 语法检查通过。此次未重复全量测试，也未使用真实 API。

### 按教程完成第一次测试

[测试速查](documents/tester_guide/README.md) · [交互教程（下载后双击 index.html）](documents/tester_guide/index.html) · [教程验证记录](documents/tester_guide/VERIFICATION.md)

GitHub 的文件页面只显示 HTML 源码；先克隆/下载仓库，再用浏览器打开 `documents/tester_guide/index.html`。保持旁边的 `assets/` 目录不变，截图才能显示。教程自身不连接应用，勾选仅记录人工测试进度。

1. 在 8502 主工作台创建管理员；按第 3 节准备产品、知识、风险词资料，并由另一账号审核、管理员发布。页面提供的模板可下载填写；教程提及的额外 `test use` 文件夹未随本仓库提供，可使用页面模板代替。
2. 在“模型服务”配置后，用“请问你们可以制作纸巾包装盒，并发货到美国加州吗？”测试 W1；未核实的信息保留待确认。W2 另选 Case 和至少一个平台，按实际产品资料生成文案；两项都需要有效 API 及额度。
3. W3 整包练习使用 [示例 ZIP](documents/tester_guide/examples/2026003%20%20%E5%B7%B2%E7%BB%8F%E5%8F%91%E8%B4%A7%E7%AD%89%E5%BE%85%E6%94%B6%E9%92%B1%281%29.zip)（约 22.9 MiB、39 份文件），原包由用户明确授权随仓库提交，未脱敏。文件名中的业务阶段不代表系统已经确认发货或到账。包的大小、校验值及说明见 [示例说明](documents/tester_guide/examples/README.md)。
4. 保持主工作台运行，另开终端进入项目根目录，用同一 Python 3.12 环境启动独立 ZIP 工作台：

Windows PowerShell：

```powershell
$env:CROSSBOT_W3_DATA = Join-Path (Get-Location).Path '.w3-data/tester-guide'
.\.venv\Scripts\python.exe -B -m streamlit run documents/tester_guide/zip_workbench.py --server.address 127.0.0.1 --server.port 8503 --server.headless true --browser.gatherUsageStats false
```

Ubuntu / WSL：

```bash
export CROSSBOT_W3_DATA="$PWD/.w3-data/tester-guide"
./.venv/bin/python -B -m streamlit run documents/tester_guide/zip_workbench.py --server.address 127.0.0.1 --server.port 8503 --server.headless true --browser.gatherUsageStats false
```

打开 [http://127.0.0.1:8503](http://127.0.0.1:8503)，首次创建此工作台自己的管理员账号（密码至少 10 位，主工作台要求至少 12 位）。**两套入口的账号、Case、资料和生成结果不互通**；`STORAGE_DIR` 不能替代 `CROSSBOT_W3_DATA`。上述目录与正式业务库隔离，重新测试可指定新的空目录，无需删除旧数据。

5. “新建交易 / 导入资料 → 导入已有资料 ZIP”，选择示例原包并“导入资料”。按教程核对 39 份原件，将来源清单设为教程列出的 12 类，交付仅选商业发票 CI、装箱单 PL，再“保存两类清单”。这是本例配置，其他交易需按实际条件调整。
6. 查看分类与原件，分别采集 CI/PL 候选，补齐字段并确认来源。处理规则缺项，先下载待核对预览；全部必需核验满足且人工确认后，分别生成并下载 CI、PL，最后可打包所选版本。复杂扫描件、照片和表格仍需人工核对；导入成功不代表审核通过。

ZIP 后端限制为包 300 MiB、单文件 20 MiB、解压总量 300 MiB、最多 2000 条目，上传组件还受 Streamlit 默认 200 MB 限制；较大合法包需在启动命令增加 `--server.maxUploadSize 300`。随附示例无需修改该选项。测试数据保存在 `.w3-data/tester-guide/`（含账号库、原件与生成文件），需要留存时停止独立工作台并备份整个目录。

## 7. 再次启动、更新和备份

再次打开终端，进入项目根目录，设置原来的 `STORAGE_DIR`（如有），执行第 2 节最后一条 Streamlit 命令。无需重建虚拟环境或管理员。

更新前备份数据，保留本地代码改动：

```bash
git status --short
git pull --ff-only
```

再用本机虚拟环境执行第 2 节的依赖安装和 `pip check` 命令，最后重启。有分支分歧或本地改动时先处理 Git 提示，不要强制覆盖。

默认数据目录：

```text
data/
├── crossbot.db       # 账号、Case、业务历史、版本和审计
├── w3-files/         # 单证原件及生成文件
└── resource-files/   # 产品/知识/风险词库上传原件
```

设置 `STORAGE_DIR` 后，数据位于自定义目录。**备份时停止应用并复制整个数据目录**，不要只复制数据库而丢失附件。恢复时将完整目录放回，指向该路径；模型 Key 需重新填写。

数据库、密钥、后续客户原件和本机备份不提交到 Git；用户授权的随附示例 ZIP 为本次明确例外。拉取仓库不会自动还原其他电脑的业务数据。

## 8. 常见问题

| 现象 | 处理 |
| --- | --- |
| 仓库不存在或无权限 | 核对 URL 和访问权限，私有仓库先配置获授权的 GitHub 凭证 |
| 找不到 Python 或启动商店占位程序 | 安装正确的 64 位 Python 并重开终端，用 `py -3.12 --version` 检查 |
| Ubuntu 缺少 ensurepip | 安装 `python3-venv`，指定版本 Python 需要匹配版本的 venv 包 |
| No module named ... | 使用指定虚拟环境重新安装依赖，不混用全局、Windows 和 WSL Python |
| pip 下载失败 | 检查网络、代理和证书，必要时使用组织批准的包镜像 |
| OCR 缺少 libGL / libgthread / libgomp | Ubuntu 安装第 2 节的 `libgl1 libglib2.0-0 libgomp1` 后重启 |
| Windows OCR/ONNX 的 DLL 加载失败 | 检查 Python 为 64 位，按 [微软官方指引](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) 安装匹配的 Visual C++ v14 x64 运行库 |
| 8502 端口占用 | 将启动端口改为 8503，访问 `http://127.0.0.1:8503` |
| 之前账号不见了 / 无法再次创建管理员 | 检查入口与 `STORAGE_DIR` 是否改变；已有账号的数据库不能再次初始化 |
| 不能生成 / 未确认发送 | 在“模型服务”补全配置并勾选确认，刷新后重新填 Key |
| API 返回 401/403、404、429 或超时 | 分别核对 Key/权限、模型及最终接口路径、额度/限流、网络 |
| 上传资料后仍找不到产品 | 草稿需另一人审核并由管理员发布，再核对适用范围和 Case 权限 |
| 上传成功但字段为空或单证不能通过 | 查看解析原因，核对格式与模板，人工补齐确认后运行规则 |
| 数据库或附件目录不可写 | 使用有写权限的本地 `STORAGE_DIR`，避免只读目录及多实例争用 |

## 9. 功能边界与开发资料

W1 支持真实询盘、多轮修订和历史，明确下单后建立未确认订单草稿。W2 只生成所选平台内容，规则摘要和风险提示不代表平台审核通过。W3 保留原件、机器候选、人工修改、版本和审计，缺失或未确认信息不得自动放行。

当前不连接外部电商订单系统，不自动发送客户消息或提交订单，也未接通真实外部搜索供应商。主界面已移除模拟工作区，旧模拟适配器和合成样例用于回归测试。`main.py`、`w3_app.py` 和 `contributions/` 中的子项目不是本文主工作台入口，子项目依赖不能代替根目录依赖。

[真实工作流](docs/real-workflow.md) · [AI 开发约束](AI开发约束.md) · [目录与协作](docs/directory-structure.md) · [公共契约](core/README.md) · [V3 服务](docs/B_V3接入说明.md)

历史文档记录当时状态，当前行为以主入口代码和测试为准。`requirements.txt` 使用版本范围，并非完整锁文件，不同日期可能安装不同依赖版本；升级后应重新执行第 6 节验收。


## 10. 可运行作品交付（hackathon-final）

最终提交以 `hackathon-final` Tag 为准。[交付说明](docs/submission/README.md) 汇总完整工程包、[无密钥平台配置导出](docs/submission/platform-config.json)、[Streamlit 配置模板](docs/submission/streamlit-config.toml)、[第三方组件声明](THIRD_PARTY_NOTICES.md) 和 [实际验证环境的依赖清单](docs/submission/third-party-components.json)。工程包内含本 README、全部源码、测试、教程和用户授权的原 ZIP；不含本机账号库或模型 Key。

[下载最终工程源码 ZIP](https://github.com/ddemfql/CrossBot/archive/refs/tags/hackathon-final.zip)，或 clone 后运行 `python docs/submission/package.py` 生成工程包及 SHA-256 清单（使用已安装的 Python 3.12）。解压后按第 1～6 节安装和启动；私有仓库下载需要读取权限。
