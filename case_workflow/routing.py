"""Idempotent queues for human handling of supplied external source records."""
from __future__ import annotations

from core.models import Actor, DocumentType as Type, WorkItem, Workspace
from documents.catalog import DOC_LABELS, EXTERNAL_TYPES
from review.policy import audit

SOURCE_ROLES = {
    Type.PURCHASE_ORDER: "sales",
    Type.PURCHASE_CONTRACT: "sales",
    Type.BILL_OF_LADING: "sales",
    Type.AIR_WAYBILL: "sales",
    Type.FORWARDER_RECEIPT: "sales",
    Type.INSURANCE_POLICY: "sales",
    Type.CERTIFICATE_OF_ORIGIN: "sales",
    Type.ISF_RECEIPT: "sales",
    Type.CUSTOMS_WORKSHEET: "sales",
    Type.ISF_WORKSHEET: "sales",
    Type.BOOKING_REQUEST: "sales",
    Type.SHIPPING_INSTRUCTION: "sales",
    Type.BANK_RECEIPT: "sales",
}


def source_task_id(document_id: str) -> str:
    """Stable identity survives task status and source version changes."""
    return "source_" + document_id


def ensure_source_tasks(workspace: Workspace, actor: Actor | None = None) -> list[WorkItem]:
    """Append missing external-file tasks, returning only newly created items.

    The caller owns authentication and repository persistence. This helper only
    queues human work; it never confirms fields, assigns an invented user,
    completes another task, approves a transaction or changes trade milestones.
    Completed tasks are retained as-is and are never duplicated or reopened.
    """
    added = []
    existing = {task.task_id for task in workspace.tasks}
    for document in workspace.documents:
        if document.deleted_at is not None:
            continue
        # Explicit source responsibility takes precedence over catalog defaults:
        # a buyer-provided empty seller template is still an external source.
        external = (document.responsibility == "external" or (
            not document.responsibility and document.document_type in EXTERNAL_TYPES))
        if not external:
            continue
        task_id = source_task_id(document.document_id)
        if task_id in existing:
            continue
        label = DOC_LABELS.get(document.document_type, document.document_type.value)
        task = WorkItem(task_id=task_id,
            title=f"核对{label}原件并处理相关业务",
            role=SOURCE_ROLES.get(document.document_type, "sales"),
            note=(f"来源文档：{document.document_id}\n文件：{document.title}\n"
                  "请核对来源方、原件真实性、单号及与本单的关系；机器分类与字段均为候选，"
                  "须真人核对后处理，不能据此推定签发、申报、到账或批准。"))
        workspace.tasks.append(task)
        existing.add(task_id)
        added.append(task)
        if actor is not None:
            audit(workspace, actor, "source_task_queued", "task", task.task_id,
                  after={"document_id": document.document_id,
                         "document_type": document.document_type.value,
                         "role": task.role},
                  reason="按资料类型进入岗位队列，未指派个人且未确认业务事实。")
    return added
