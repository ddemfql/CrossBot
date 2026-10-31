"""Serialize B's domain sessions into A's Case-scoped conversation store."""
from __future__ import annotations

import json
from core.models import from_dict, to_dict
from core.schemas import GenerationResponse, ListingRequest, WorkflowConversation
from workflows.conversation import SalesConversation

SALES_KEYS = (
    "sales_inquiry", "sales_product", "sales_language", "sales_country", "sales_quantity",
    "sales_candidates", "customer_simulation", "customer_topic", "sales_conversation",
    "sales_feedback", "legacy_sales_messages",
)
LISTING_KEYS = (
    "listing_description", "listing_platforms", "listing_language", "listing_country",
    "listing_request", "listing_results", "legacy_listing_messages",
)


def _stored_value(value):
    if value is None:
        return None
    return to_dict(value)


def _snapshot(session_state, workflow):
    keys = SALES_KEYS if workflow == "sales" else LISTING_KEYS
    state = {key: _stored_value(session_state.get(key)) for key in keys if key in session_state}
    if workflow == "sales" and "sales_candidates" in state:
        inquiry, fields = session_state.sales_candidates
        state["sales_candidates"] = {"inquiry": inquiry, "fields": to_dict(fields)}
    return state


def bind_case_state(access, token, session_state, case_id, workflow):
    marker = f"_workflow_{workflow}_case_id"
    if session_state.get(marker) == case_id:
        return
    keys = SALES_KEYS if workflow == "sales" else LISTING_KEYS
    for key in keys:
        session_state.pop(key, None)
    session_state[marker] = case_id
    conversation = access.get_conversation(token, case_id, workflow)
    data = conversation.messages[0] if conversation.messages else {}
    if data and ("role" in data or "content" in data):
        session_state[f"legacy_{workflow}_messages"] = conversation.messages
        data = {}
    for key, value in data.items():
        if key == "sales_conversation" and value:
            session_state[key] = from_dict(SalesConversation, value)
        elif key == "sales_candidates" and value:
            from core.models import ExtractedField
            session_state[key] = (value["inquiry"], [from_dict(ExtractedField, item) for item in value["fields"]])
        elif key == "customer_simulation" and value:
            session_state[key] = from_dict(GenerationResponse, value)
        elif key == "listing_request" and value:
            session_state[key] = from_dict(ListingRequest, value)
        elif key == "listing_results" and value:
            session_state[key] = {platform: from_dict(GenerationResponse, result) for platform, result in value.items()}
        else:
            session_state[key] = value
    session_state[f"_workflow_{workflow}_revision"] = conversation.revision
    session_state[f"_workflow_{workflow}_snapshot"] = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)


def persist_case_workflow(store, access, token, session_state, case_id, workflow):
    data = _snapshot(session_state, workflow)
    fingerprint = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
    snapshot_key = f"_workflow_{workflow}_snapshot"
    if fingerprint == session_state.get(snapshot_key):
        return False
    revision_key = f"_workflow_{workflow}_revision"
    conversation = WorkflowConversation(
        case_id=case_id, workflow=workflow, messages=[data],
        revision=session_state.get(revision_key, 0),
    )
    access.save_conversation(token, conversation)
    session_state[revision_key] = conversation.revision
    session_state[snapshot_key] = fingerprint
    return True
