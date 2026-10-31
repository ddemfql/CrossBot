"""Transaction-level orchestration; rules and persistence are injected ports."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (Actor, Case, CaseStatus, CrossCheckStatus, DocumentType,
                         PaymentRecord, ReceivableStatus, RequirementPolicy, TradeData,
                         TradeMilestone, WorkItem, Workspace, to_dict, utc_now)
from review.policy import (ROLES, audit, current_artifacts, has_current_approval,
                           invalidate, require)


def _reason(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("请填写本次操作的原因或说明。")
    return value.strip()


def _decimal(value, label: str, *, positive=False) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result == 0):
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{label}必须是有效的{'正' if positive else '非负'}数。") from None


def _iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{label}需填写有效日期（YYYY-MM-DD）。") from None


def trade_total(trade: TradeData) -> Decimal:
    return sum((_decimal(line.quantity, "数量", positive=True)
                * _decimal(line.unit_price, "单价") for line in trade.line_items), Decimal("0"))


def validate_trade_lines(trade: TradeData) -> None:
    for line in trade.line_items:
        if not line.description.strip() or not line.product_code.strip() or not line.unit.strip():
            raise ValidationError("每项产品必须填写编码、品名和单位。")
        _decimal(line.quantity, "数量", positive=True)
        _decimal(line.unit_price, "单价")
        gross = _decimal(line.gross_weight_kg, "毛重")
        net = _decimal(line.net_weight_kg, "净重")
        if gross < net:
            raise ValidationError("毛重不能小于净重。")
        if isinstance(line.packages, bool) or not isinstance(line.packages, int) or line.packages < 0:
            raise ValidationError("箱数必须是非负整数。")


def validate_draft_lines(trade: TradeData) -> None:
    """Allow missing draft data, but reject malformed supplied numbers."""
    from documents.input_validation import validate_input_text
    validate_input_text(trade)
    for line in trade.line_items:
        for field, label, positive in (("quantity", "数量", True),
                ("unit_price", "单价", False), ("gross_weight_kg", "毛重", False),
                ("net_weight_kg", "净重", False)):
            value = getattr(line, field)
            if value is not None and str(value).strip():
                _decimal(value, label, positive=positive)
        if (line.gross_weight_kg is not None and line.net_weight_kg is not None
                and str(line.gross_weight_kg).strip() and str(line.net_weight_kg).strip()
                and _decimal(line.gross_weight_kg, "毛重") < _decimal(line.net_weight_kg, "净重")):
            raise ValidationError("毛重不能小于净重。")
        if isinstance(line.packages, bool) or not isinstance(line.packages, int) or line.packages < 0:
            raise ValidationError("箱数必须是非负整数。")


    for adjustment in trade.adjustments:
        if str(adjustment.amount).strip():
            _decimal(adjustment.amount, "费用或调整金额")
        if not isinstance(adjustment.included_in_invoice_price, bool):
            raise ValidationError("费用是否已含在单价中的标记无效。")
        if adjustment.currency and (len(adjustment.currency) != 3
                or not adjustment.currency.isascii() or not adjustment.currency.isalpha()
                or adjustment.currency != adjustment.currency.upper()):
            raise ValidationError("费用币种需使用三位大写代码。")
    for package in trade.packages:
        for name in ("quantity", "gross_weight_kg", "net_weight_kg"):
            if str(getattr(package, name)).strip():
                _decimal(getattr(package, name), "分箱数量或重量")
        if package.gross_weight_kg and package.net_weight_kg and (
                _decimal(package.gross_weight_kg, "分箱毛重") < _decimal(package.net_weight_kg, "分箱净重")):
            raise ValidationError("分箱毛重不能小于净重。")


def validate_trade(trade: TradeData) -> None:
    for key, label in (("order_no", "订单号"), ("seller", "卖方"), ("buyer", "买方"),
                       ("destination_country", "目的国"), ("currency", "币种"),
                       ("incoterm", "贸易术语"), ("payment_terms", "付款条件"),
                       ("transport_mode", "运输方式")):
        if not isinstance(getattr(trade, key), str) or not getattr(trade, key).strip():
            raise ValidationError(f"请先补充{label}。")
    if len(trade.currency) != 3 or not trade.currency.isalpha() or trade.currency != trade.currency.upper():
        raise ValidationError("币种需使用三位大写代码，例如 USD。")
    if not trade.line_items:
        raise ValidationError("请至少添加一项产品。")
    validate_trade_lines(trade)
    for field in ("expected_ship_date", "actual_ship_date", "due_date", "transaction_date"):
        value = getattr(trade, field)
        if value:
            _iso_date(value, "日期")


def requirements(trade: TradeData, base: list[DocumentType],
                 policies: list[RequirementPolicy]) -> list[DocumentType]:
    result = list(base)
    for policy in policies:
        applicable = all(not getattr(policy, key) or
                         getattr(policy, key).strip().casefold() == getattr(trade, key).strip().casefold()
                         for key in ("destination_country", "product_category", "transport_mode", "incoterm", "payment_terms"))
        if applicable:
            result.extend(policy.required_types)
    return list(dict.fromkeys(DocumentType(item) for item in result))


def receivable(workspace: Workspace) -> dict[str, str]:
    total = trade_total(workspace.trade)
    paid = sum((_decimal(p.amount, "收款金额") for p in workspace.payments), Decimal("0"))
    balance = total - paid
    status = ReceivableStatus.PAID if balance == 0 and workspace.payments else (
        ReceivableStatus.PARTIAL if paid > 0 else ReceivableStatus.UNPAID)
    if balance > 0 and workspace.trade.due_date and _iso_date(workspace.trade.due_date, "到期日") < date.today():
        status = ReceivableStatus.OVERDUE
    return {"total": str(total), "paid": str(paid), "balance": str(balance),
            "currency": workspace.trade.currency, "status": status.value}


def sync_check_status(workspace: Workspace) -> None:
    workspace.case.cross_check_result_ids = [c.result_id for c in workspace.checks]
    if any(c.status is CrossCheckStatus.FAIL for c in workspace.checks):
        workspace.case.status = CaseStatus.CROSS_CHECK_FAILED
        return
    types = {d.document_type for d in workspace.documents
             if d.current_version_id and d.deleted_at is None}
    types.update(a.document_type for a in current_artifacts(workspace))
    if any(t not in types for t in workspace.required_document_types):
        workspace.case.status = CaseStatus.MISSING_DOCUMENTS
    else:
        workspace.case.status = CaseStatus.PENDING_REVIEW


class CaseService:
    def __init__(self, repository, rules, identity=None, defaults_store=None):
        self.repository, self.rules = repository, rules
        self.identity = identity
        self.defaults_store = defaults_store

    def _load(self, case_id: str, actor: Actor, action: str) -> Workspace:
        workspace = self.repository.get(case_id)
        try:
            require(workspace, actor, action)
        except CrossBotError as exc:
            if exc.code is ErrorCode.FORBIDDEN:
                self.repository.save(workspace, expected_revision=workspace.revision)
            raise
        return workspace

    @staticmethod
    def _visible(workspace: Workspace, actor: Actor) -> Workspace:
        """Hide logically removed documents at the service boundary for non-admins."""
        if "admin" in actor.roles:
            return workspace
        workspace.documents = [document for document in workspace.documents
                              if document.deleted_at is None]
        workspace.case.document_ids = [document.document_id for document in workspace.documents]
        return workspace

    def _save(self, workspace: Workspace) -> Workspace:
        # The merged shared Case contract also exposes requirements. Workspace
        # remains the configurable source of truth for this transaction.
        workspace.case.required_document_types = list(workspace.required_document_types)
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace

    def create(self, actor: Actor, trade: TradeData,
               required_types: list[DocumentType] | None = None,
               policies: list[RequirementPolicy] | None = None) -> Workspace:
        require(None, actor, "create")
        if not isinstance(trade, TradeData):
            raise ValidationError("订单资料格式不正确。")
        trade = deepcopy(trade)
        validate_draft_lines(trade)
        trade.revision, trade.confirmed_by, trade.confirmed_at = 1, None, None
        case = Case(order_no=trade.order_no, customer_name=trade.buyer,
                    destination_country=trade.destination_country, currency=trade.currency,
                    incoterm=trade.incoterm, owner_id=actor.user_id)
        case.case_number = "W3-" + case.case_id.removeprefix("case_")[:12].upper()
        policies = deepcopy(policies or [])
        base = list(required_types) if required_types is not None else [
            DocumentType.COMMERCIAL_INVOICE, DocumentType.PACKING_LIST]
        defaults = self.defaults_store.get(actor.user_id) if self.defaults_store is not None else None
        if defaults is not None:
            from .preferences import validate_defaults
            validate_defaults(defaults)
            if required_types is None:
                base = list(dict.fromkeys([*defaults.source_types, *defaults.delivery_types]))
        workspace = Workspace(case=case, trade=trade,
                              base_required_document_types=list(dict.fromkeys(DocumentType(t) for t in base)),
                              requirement_policies=policies,
                              required_document_types=requirements(trade, base, policies))
        if defaults is not None:
            from .preferences import apply_document_choices
            if required_types is None:
                workspace.required_source_types = list(defaults.source_types)
                workspace.required_delivery_types = list(defaults.delivery_types)
            apply_document_choices(workspace, defaults,
                kinds=set(DocumentType(kind) for kind in base) if required_types is not None else None)
        from documents.forms import get_form
        from documents.catalog import SELLER_TYPES
        for kind in workspace.required_document_types:
            if kind in SELLER_TYPES and not any(form.document_type is kind for form in workspace.document_forms):
                workspace.document_forms.append(get_form(workspace, kind))
        workspace.tasks.append(WorkItem(title="补充并确认订单资料", role="sales", assignee_id=actor.user_id))
        audit(workspace, actor, "case_created", "case", case.case_id,
              after={"trade": to_dict(trade), "required_types": to_dict(workspace.required_document_types)})
        return self._save(workspace)

    def get(self, case_id: str, actor: Actor) -> Workspace:
        return self._visible(self._load(case_id, actor, "view"), actor)

    def list(self, actor: Actor) -> list[Workspace]:
        require(None, actor, "view")
        return [self._visible(workspace, actor) for workspace in self.repository.list()]

    def update_trade(self, case_id: str, actor: Actor, trade: TradeData, reason: str,
                     expected_revision=None) -> Workspace:
        workspace = self._load(case_id, actor, "update_trade")
        if expected_revision is not None and workspace.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已被修改，请刷新后重新填写。")
        reason = _reason(reason)
        if not isinstance(trade, TradeData):
            raise ValidationError("订单资料格式不正确。")
        trade = deepcopy(trade)
        from documents.input_validation import validate_input_text
        validate_input_text(trade)
        validate_draft_lines(trade)
        before = to_dict(workspace.trade)
        if workspace.payments:
            old = receivable(workspace)
            if trade.currency != workspace.trade.currency or trade_total(trade) < Decimal(old["paid"]):
                raise ValidationError("已有收款时不可变更币种或把订单金额改到已收款金额以下。")
        trade.revision = workspace.trade.revision + 1
        trade.confirmed_by, trade.confirmed_at = None, None
        workspace.trade = trade
        workspace.case.order_no, workspace.case.customer_name = trade.order_no, trade.buyer
        workspace.case.destination_country, workspace.case.currency = trade.destination_country, trade.currency
        workspace.case.incoterm = trade.incoterm
        # Policies are retained and re-evaluated when the destination or terms change.
        workspace.required_document_types = requirements(
            trade, workspace.base_required_document_types, workspace.requirement_policies)
        for document in workspace.documents:
            document.required = document.document_type in workspace.required_document_types
        invalidate(workspace, actor, reason)
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "trade_updated", "trade", case_id,
              before=before, after=to_dict(trade), reason=reason)
        return self._save(workspace)

    def set_required_documents(self, case_id: str, actor: Actor, required_types,
                               reason: str = "调整所需文件") -> Workspace:
        """Change this transaction's checklist, retaining applicable policies."""
        workspace = self._load(case_id, actor, "update_trade")
        reason = _reason(reason)
        try:
            selected = list(dict.fromkeys(DocumentType(kind) for kind in required_types))
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的文件类型。") from exc
        before = list(workspace.required_document_types)
        workspace.base_required_document_types = selected
        # Legacy callers replace the whole checklist, so discard prior purpose
        # selections rather than silently retaining stale explicit groups.
        workspace.required_source_types = None
        workspace.required_delivery_types = None
        workspace.required_document_types = requirements(
            workspace.trade, selected, workspace.requirement_policies)
        for document in workspace.documents:
            document.required = document.document_type in workspace.required_document_types
        invalidate(workspace, actor, reason)
        audit(workspace, actor, "required_documents_updated", "case", case_id,
              before=to_dict(before), after=to_dict(workspace.required_document_types), reason=reason)
        return self._save(workspace)

    def set_document_checklist(self, case_id: str, actor: Actor, source_types,
                               delivery_types, reason: str = "调整资料与交付文件清单",
                               expected_revision=None) -> Workspace:
        """Select input and output purposes without requiring uploaded originals."""
        from documents.catalog import SELLER_TYPES

        workspace = self._load(case_id, actor, "update_trade")
        if expected_revision is not None and workspace.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料清单已被修改，请刷新后重新选择。")
        reason = _reason(reason)

        def selected(values):
            if not isinstance(values, (list, tuple)):
                raise ValidationError("文件清单须为文件类型列表。")
            try:
                return list(dict.fromkeys(DocumentType(kind) for kind in values))
            except (ValueError, TypeError) as exc:
                raise ValidationError("请选择有效的文件类型。") from exc

        sources, deliveries = selected(source_types), selected(delivery_types)
        if any(kind not in SELLER_TYPES for kind in deliveries):
            raise ValidationError("交付制作清单只能选择我方可制作的文件；外部签发文件请列为需要提供的资料。")
        before = {"source_types": to_dict(workspace.required_source_types),
                  "delivery_types": to_dict(workspace.required_delivery_types),
                  "required_types": to_dict(workspace.required_document_types)}
        workspace.required_source_types = sources
        workspace.required_delivery_types = deliveries
        workspace.base_required_document_types = list(dict.fromkeys([*sources, *deliveries]))
        workspace.required_document_types = requirements(workspace.trade,
            workspace.base_required_document_types, workspace.requirement_policies)
        for document in workspace.documents:
            document.required = document.document_type in workspace.required_document_types
        invalidate(workspace, actor, reason)
        audit(workspace, actor, "document_checklist_updated", "case", case_id,
              before=before, after={"source_types": to_dict(sources),
                  "delivery_types": to_dict(deliveries),
                  "required_types": to_dict(workspace.required_document_types)}, reason=reason)
        return self._save(workspace)

    def confirm_trade(self, case_id: str, actor: Actor, reason: str) -> Workspace:
        workspace = self._load(case_id, actor, "confirm_trade")
        reason = _reason(reason)
        validate_trade(workspace.trade)
        if workspace.trade.confirmed_by:
            return workspace
        workspace.trade.confirmed_by, workspace.trade.confirmed_at = actor.user_id, utc_now()
        for task in workspace.tasks:
            if task.title == "补充并确认订单资料" and task.status == "pending":
                task.status = "completed"
        # A single business user continues without a role handoff.
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "trade_confirmed", "trade", case_id,
              after={"input_revision": workspace.trade.revision}, reason=reason)
        return self._save(workspace)

    def check(self, case_id: str, actor: Actor) -> Workspace:
        workspace = self._load(case_id, actor, "check")
        old_status = workspace.case.status
        workspace.checks = self.rules.evaluate(workspace)
        sync_check_status(workspace)
        if old_status is CaseStatus.PASSED and has_current_approval(workspace):
            workspace.case.status = CaseStatus.PASSED
        audit(workspace, actor, "rules_checked", "case", case_id,
              after={"status": workspace.case.status.value,
                     "results": [{"rule": c.rule_code, "status": c.status.value} for c in workspace.checks]})
        return self._save(workspace)

    def handoff(self, case_id: str, actor: Actor, title: str, role: str,
                assignee_id: str | None = None, note: str = "",
                source_task_id: str | None = None) -> Workspace:
        workspace = self._load(case_id, actor, "handoff")
        if not title.strip() or role not in ROLES:
            raise ValidationError("请填写交接任务并选择有效岗位。")
        if assignee_id:
            if self.identity is None:
                raise ValidationError("具体办理人须通过工作区账号目录验证。")
            user = next((u for u in self.identity.list_users() if u["user_id"] == assignee_id), None)
            if user is None or not ({role, "admin"} & set(user["roles"])):
                raise ValidationError("办理人不存在或不具备目标岗位。")
        if source_task_id:
            source = self._owned_task(workspace, actor, source_task_id)
            source.status = "completed"
        task = WorkItem(title=title.strip(), role=role, assignee_id=assignee_id, note=note)
        workspace.tasks.append(task)
        audit(workspace, actor, "task_handed_off", "task", task.task_id, after=to_dict(task), reason=note)
        return self._save(workspace)

    @staticmethod
    def _owned_task(workspace, actor, task_id):
        task = next((task for task in workspace.tasks if task.task_id == task_id), None)
        if task is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "待办不存在。")
        if task.status != "pending":
            raise ValidationError("该待办已处理，请刷新。")
        if "admin" not in actor.roles and (task.assignee_id != actor.user_id
                if task.assignee_id else task.role not in actor.roles):
            raise CrossBotError(ErrorCode.FORBIDDEN, "只能处理本人或本岗位未指派的待办。")
        return task

    def complete_task(self, case_id: str, actor: Actor, task_id: str, reason: str) -> Workspace:
        workspace = self._load(case_id, actor, "handoff")
        reason = _reason(reason)
        task = self._owned_task(workspace, actor, task_id)
        task.status = "completed"
        audit(workspace, actor, "task_completed", "task", task.task_id,
              after=to_dict(task), reason=reason)
        return self._save(workspace)

    def set_milestone(self, case_id: str, actor: Actor, milestone: TradeMilestone,
                      reason: str, shipped_on: str | None = None, reference: str = "") -> Workspace:
        workspace = self._load(case_id, actor, "milestone")
        reason = _reason(reason)
        try:
            milestone = TradeMilestone(milestone)
        except ValueError:
            raise ValidationError("交易环节不正确。") from None
        order = list(TradeMilestone)
        current, target = order.index(workspace.milestone), order.index(milestone)
        if target < current:
            raise ValidationError("已发生的运输节点不能倒退，请通过审计说明更正。")
        if milestone in {TradeMilestone.SHIPPED, TradeMilestone.DELIVERED}:
            checks = self.rules.evaluate(workspace)
            if (workspace.case.status is not CaseStatus.PASSED or not checks
                    or any(c.status is not CrossCheckStatus.PASS for c in checks)
                    or not has_current_approval(workspace, checks)):
                raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "需先完成人工批准和规则检查，才能记录发货。")
            if milestone is TradeMilestone.DELIVERED and workspace.milestone is not TradeMilestone.SHIPPED:
                raise ValidationError("请先登记实际发货，再登记送达。")
            if milestone is TradeMilestone.SHIPPED:
                if not reference.strip():
                    raise ValidationError("请填写实际运输凭证或提单参考号。")
                actual_date = _iso_date(shipped_on or date.today().isoformat(), "发货日期")
                if actual_date > date.today():
                    raise ValidationError("实际发货日期不能在未来。")
                workspace.shipped_on = actual_date.isoformat()
                workspace.shipment_reference = reference.strip()
        before = workspace.milestone.value
        workspace.milestone = milestone
        audit(workspace, actor, "milestone_recorded", "trade", case_id,
              before={"milestone": before},
              after={"milestone": milestone.value, "recorded_on": date.today().isoformat(),
                     "shipped_on": workspace.shipped_on,
                     "shipment_reference": workspace.shipment_reference}, reason=reason)
        return self._save(workspace)

    def record_payment(self, case_id: str, actor: Actor, amount: str, currency: str,
                       reference: str, received_on: str) -> Workspace:
        workspace = self._load(case_id, actor, "payment")
        amount_value = _decimal(amount, "收款金额", positive=True)
        if currency != workspace.trade.currency:
            raise ValidationError("收款币种必须与订单币种一致。")
        if not reference.strip():
            raise ValidationError("请填写收款凭证或流水参考号。")
        received_date = _iso_date(received_on, "收款日期")
        if received_date > date.today():
            raise ValidationError("实际收款日期不能在未来。")
        if not workspace.trade.confirmed_by:
            raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "请先确认订单金额，再登记收款。")
        if amount_value > Decimal(receivable(workspace)["balance"]):
            raise ValidationError("收款金额不能超过未收余额。")
        if any(p.reference == reference.strip() for p in workspace.payments):
            raise CrossBotError(ErrorCode.CONFLICT, "该收款凭证已登记，请勿重复记账。")
        payment = PaymentRecord(amount=str(amount_value), currency=currency,
                                reference=reference.strip(), received_on=received_date.isoformat(),
                                recorded_by=actor.user_id)
        workspace.payments.append(payment)
        audit(workspace, actor, "payment_recorded", "payment", payment.payment_id, after=to_dict(payment))
        return self._save(workspace)

    def close(self, case_id: str, actor: Actor, reason: str) -> Workspace:
        workspace = self._load(case_id, actor, "close")
        reason = _reason(reason)
        checks = self.rules.evaluate(workspace)
        if (workspace.case.status is not CaseStatus.PASSED or not checks
                or any(c.status is not CrossCheckStatus.PASS for c in checks)
                or not has_current_approval(workspace, checks)):
            raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "全部关键资料需通过规则检查和当前版本人工批准后才能关闭。")
        if workspace.milestone not in {TradeMilestone.SHIPPED, TradeMilestone.DELIVERED}:
            raise ValidationError("请先登记发货，再关闭归档。")
        if Decimal(receivable(workspace)["balance"]) != 0:
            raise ValidationError("仍有未收余额，请完成收款后关闭归档。")
        workspace.case.status = CaseStatus.CLOSED
        for task in workspace.tasks:
            task.status = "completed"
        audit(workspace, actor, "case_closed", "case", case_id, reason=reason)
        return self._save(workspace)

    receivable = staticmethod(receivable)
