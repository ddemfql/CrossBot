# CrossBot 公共契约使用说明

## 状态流转

```python
from core.models import CaseStatus
from core.validators import transition_case

transition_case(case, CaseStatus.PENDING_REVIEW)
transition_case(case, CaseStatus.PASSED, review_tasks=tasks, checks=checks)
transition_case(case, CaseStatus.CLOSED, review_tasks=tasks, checks=checks)
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

存储层以 JSON 保存模型内容，读回时会恢复枚举、时间、Decimal 和嵌套模型。

## 规则

- 业务模块依赖 `core`，不复制模型。
- 通过 `core.models.to_dict` 和 `core.models.from_dict` 做序列化。
- 页面层不直接访问 SQLite。
- 金额使用 `Decimal`，不要使用 `float` 做最终比对。
- 缺失、未知和未复核状态不能转换成通过。
