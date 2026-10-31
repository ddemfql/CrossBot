"""Bind W3 operations to the shared server session and selected Case."""
from contextlib import contextmanager

from core.errors import CrossBotError, ErrorCode
from core.identity import Permission, Role
from core.models import Actor
from .policy import authorization_boundary


ACTION_PERMISSIONS = {
    "view": Permission.CASE_READ,
    "download": Permission.ATTACHMENT_READ,
    "create": Permission.CASE_CREATE,
    "update_trade": Permission.CASE_UPDATE,
    "edit_trade": Permission.CASE_UPDATE,
    "manual_field": Permission.CASE_UPDATE,
    "upload": Permission.ATTACHMENT_WRITE,
    "parse": Permission.ATTACHMENT_WRITE,
    "generate": Permission.CASE_UPDATE,
    "delete_document": Permission.ATTACHMENT_WRITE,
    "confirm_field": Permission.ATTACHMENT_CONFIRM,
    "confirm_trade": Permission.ATTACHMENT_CONFIRM,
    "approve": Permission.CASE_REVIEW,
    "return": Permission.CASE_REVIEW,
    "close": Permission.CASE_CLOSE,
    "milestone": Permission.CASE_UPDATE,
    "payment": Permission.CASE_UPDATE,
    "handoff": Permission.CASE_ASSIGN,
    "check": Permission.CROSS_CHECK,
    "template_manage": Permission.USER_MANAGE,
}


@contextmanager
def shared_w3_session(auth, token, case_id):
    principal = auth.require_permission(token, Permission.CASE_READ, case_id)
    roles = ["admin"] if principal.active_role == Role.ADMIN else ["sales"]
    actor = Actor(principal.user_id, roles, authenticated=True)

    def authorize(workspace, supplied_actor, action):
        permission = ACTION_PERMISSIONS.get(action)
        if permission is None or (workspace is not None and workspace.case.case_id != case_id):
            raise CrossBotError(ErrorCode.PERMISSION_DENIED, "W3 操作或 Case 不在当前授权范围内")
        current = auth.require_permission(token, permission, case_id)
        if (not isinstance(supplied_actor, Actor) or supplied_actor != actor
                or current.user_id != principal.user_id
                or current.active_role != principal.active_role):
            raise CrossBotError(ErrorCode.PERMISSION_DENIED, "身份或岗位已变化，请刷新工作台")

    scope = authorization_boundary.set(authorize)
    try:
        yield actor
    finally:
        authorization_boundary.reset(scope)
