# CrossBot AI 开发约束

> 适用对象：参与 CrossBot 开发的 AI 编程助手和开发成员  
> 适用分支：`feature/foundation-contracts` 及其派生分支  
> 当前阶段：W1/W2 稳定化 + W3 单证核验最小闭环

## 一、先阅读再修改

V3 公共基线已扩展：还必须阅读 `docs/B_V3接入说明.md`。
登录、Case 分配、敏感数据读取、生成、附件和知识发布应调用文档所列的受权服务。
`SQLiteStore` 是内部持久化适配，不是面向不可信请求的安全接口。

开始任何修改前，必须阅读根目录 `README.md`、`docs/directory-structure.md`、`core/README.md`，并检查当前分支状态、最近提交和已有未提交改动。不得根据文件名猜测功能，也不得把 README 描述直接当作已实现功能。

## 二、目录和分支约束

新代码必须放在对应新目录：

| 功能 | 目录 | 分支 |
|---|---|---|
| 公共模型、事件、错误、配置 | `core/` | `feature/foundation-contracts` |
| W1/W2 工作流 | `workflows/` | `feature/w1-w2-platform` |
| API 和 Mock 适配 | `providers/` | `feature/w1-w2-platform` |
| 结构化知识库和检索 | `knowledge/` | `feature/w1-w2-platform` |
| W3 Case 和状态流转 | `case_workflow/` | `feature/w3-document-workflow` |
| PDF/Excel 上传与解析 | `documents/` | `feature/w3-document-workflow` |
| 人工复核 | `review/` | `feature/w3-document-workflow` |
| 持久化 | `storage/` | 按模块协同，默认先用 SQLite |
| 交叉比对和规则测试 | `validation/`、`tests/` | `feature/cross-check-quality` |

旧目录仅用于迁移参考：`main.py`、`utils/`、`knowledge_base/`、`prompts/`。迁移完成前不得删除；不得在旧目录增加新功能；不得同时维护两套同名业务实现。

## 三、公共契约必须优先遵守

跨模块通信必须使用 `core/` 中的模型、事件和错误类型：`core.models`、`core.schemas`、`core.events`、`core.errors`、`core.config`。

禁止使用以下方式作为正式接口：

- 依赖固定换行的字符串。
- 依赖列表位置的匿名字典。
- 页面层自行约定的特殊字符串标记。
- 把错误文本伪装成模型正文。
- 在多个模块复制不同版本的数据类。

如果公共契约不够用，先提出契约变更，更新 `core/` 和测试，再修改业务模块。

## 四、W1/W2 约束

### W1 询盘工作流

必须明确传递客户询盘、中文产品信息、目标语言、客户国家（如已知）、数量、交期和其他已知条件。输出必须区分客户可见商务回复、内部风险提示和待确认信息。知识库没有依据的认证、证书编号、规格、交期或价格不得由 AI 补写。

### W2 Listing 工作流

必须明确传递产品描述、用户选择的平台、目标市场和语言（如已知）。只生成用户选择的平台内容，平台为空时阻止提交，平台规则必须进入检索或校验流程。

### 多轮修改

首次请求和后续修改必须使用不同的请求类型或状态标记。不得通过字符串行号判断请求类型。每次修改都必须重新执行合规检查。

## 五、知识库约束

知识库条目应包含内容、类型、来源、版本、生效日期、适用国家或平台、适用产品范围。检索结果必须返回来源和适用条件；没有命中时返回未知状态，不得把相似内容当作确定事实。当前阶段优先使用结构化 JSON/CSV 查询，后续再根据规模决定是否引入向量检索。

## 六、W3 单证工作流约束

W3 的通过条件必须由确定性规则和人工确认共同决定，AI 不得直接批准 Case 或单据。

Case 至少使用以下状态：

```text
草稿
资料待补
解析中
待复核
复核退回
交叉比对失败
通过
关闭
```

第一阶段优先支持商业发票 PDF、装箱单 Excel、文本型 PDF、`.xlsx` 和固定模板。解析字段必须保留来源位置：PDF 页码，Excel 工作表和单元格地址。机器提取值、人工修改值和最终确认值必须分开保存。人工修改必须记录人员、时间、原值、新值和理由。关键字段未复核时，Case 不得进入通过或关闭状态。

## 七、交叉比对约束

交叉比对必须使用 Python 确定性规则，不调用大模型裁决。第一阶段优先比对：

1. 商业发票与装箱单：品名、数量、箱数、毛重、净重。
2. 商业发票与合同：客户、币种、金额、贸易条款。
3. 装箱单与提单：件数、重量、收发货人。
4. Case 基础信息与全部单据：客户、国家、订单号。

结果必须区分：

```text
PASS
FAIL
PENDING_REVIEW
```

缺失字段和无法确定的字段必须是 `PENDING_REVIEW`，不能当作一致。每个差异必须包含两侧值、规则编号、文档版本和字段来源。

## 八、文件和数据安全约束

- 不读取、打印、提交或修改 `.env` 中的密钥。
- 不把真实客户敏感资料写进测试样例或日志。
- 上传文件必须记录哈希和版本。
- 原文件不得被解析结果覆盖。
- 解析失败必须保留失败状态和原因。
- 不使用破坏性 Git 命令覆盖他人改动。
- 发现未提交改动时先查看并保留，不得擅自清理。

## 九、编码和测试约束

中文文件统一使用 UTF-8。优先使用标准库和项目已有依赖；新增依赖必须说明原因并更新 `requirements.txt`。公共模型变更必须增加测试。关键业务规则必须覆盖正常、缺失、冲突和格式异常场景。完成修改后至少执行语法检查和相关测试。

建议命令：

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m py_compile <changed_files>
git status --short
```

## 十、提交和交接要求

每个分支提交前必须说明：

1. 修改了哪些文件。
2. 实现了什么功能。
3. 使用了哪些公共契约。
4. 测试命令和结果。
5. 已知限制。
6. 是否需要其他分支同步契约。

提交信息使用清晰的动词，例如：

```text
feat: add structured invoice field parser
fix: route listing platforms through request schema
refactor: migrate sales workflow to core contracts
test: cover cross-document quantity mismatch
```

公共契约更新顺序：

```text
feature/foundation-contracts
  -> 其他功能分支同步
  -> 各功能分支实现
  -> integration/phase-1 集成
```

## 十一、完成标准

一次任务只有同时满足以下条件才算完成：

- 代码已写入正确的新目录。
- 未破坏现有无关功能。
- 使用了公共契约。
- 有对应的失败场景处理。
- 相关测试通过。
- 交接信息完整。
- 没有把未知信息伪装成确定结果。
- W3 的单证通过状态经过人工复核和 Python 规则双重确认。

如果需求与以上约束冲突，先指出冲突点，再选择对现有数据和业务状态影响最小的实现方式。
