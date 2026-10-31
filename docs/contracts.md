# CrossBot 公共契约使用说明

V3 新增身份、权限、附件、搜索、知识版本与生成记录的服务入口，
详见 [V3 接入说明](B_V3接入说明.md)。本文原始存储示例仅适用于内部持久化，
面向用户请求应调用 AuthService / CaseAccessService 等服务进行核权。

## 状态流转

```python
from core.models import CaseStatus
from core.validators import transition_case

case, audit = transition_case(case, CaseStatus.PENDING_REVIEW, documents=documents)
case, audit = transition_case(case, CaseStatus.PASSED, documents=documents, review_tasks=tasks, checks=checks, actor_id="reviewer-1")
case, audit = transition_case(case, CaseStatus.CLOSED, documents=documents, review_tasks=tasks, checks=checks, actor_id="reviewer-1")
```

`PASSED` 和 `CLOSED` 必须同时满足：至少一个必需人工复核任务为 `APPROVED`，并且至少一个交叉比对结果为 `PASS`，所有任务和结果都不能处于未完成状态。

## SQLite 存储

```python
from storage import SQLiteStore

store = SQLiteStore("data/crossbot.db")
store.save_case(case)
case = store.get_case(case_id)
store.save_document(document)
store.save_review_task(task)
store.save_cross_check(result)
store.append_audit(event)
```

存储层以 JSON 保存模型内容，读回时会恢复枚举、时间、Decimal 和嵌套模型。`find_document_by_sha256` 可用于阻止同一 Case 重复上传同一文件。

## 规则

- 业务模块依赖 `core`，不复制模型。
- 通过 `core.models.to_dict` 和 `core.models.from_dict` 做序列化。
- 页面层不直接访问 SQLite。
- 金额使用 `Decimal`，不要使用 `float` 做最终比对。
- 缺失、未知和未复核状态不能转换成通过。
- Case 通过前必须具备全部 `required_document_types`，并使用每个文档的 `current_version_id` 做校验。
- 状态转换返回更新后的 Case 和对应的 `AuditEvent`，调用方应保存该审计事件。
