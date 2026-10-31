"""Transactional W1 conversation state, separate from UI reruns and business contracts."""
from copy import deepcopy
from dataclasses import dataclass, field

from core.errors import ValidationError
from core.schemas import GenerationResponse, SalesRequest
from providers.contracts import ChatClient
from workflows.sales import generate_sales


@dataclass
class SalesTurn:
    kind: str
    feedback: str | None
    response: GenerationResponse


@dataclass
class SalesConversation:
    request: SalesRequest
    turns: list[SalesTurn] = field(default_factory=list)


def start_sales(request: SalesRequest, client: ChatClient) -> SalesConversation:
    snapshot = deepcopy(request)
    response = generate_sales(snapshot, client)
    return SalesConversation(snapshot, [SalesTurn("initial", None, response)])


def revise_sales(conversation: SalesConversation, feedback: str, client: ChatClient) -> SalesConversation:
    if not conversation.turns:
        raise ValidationError("请先生成首次草稿。")
    response = generate_sales(conversation.request, client, feedback=feedback,
                              previous_content=conversation.turns[-1].response.content)
    # The old successful state is left intact on any failure; never append error prose as a draft.
    result = deepcopy(conversation)
    result.turns.append(SalesTurn("revision", feedback, response))
    return result
