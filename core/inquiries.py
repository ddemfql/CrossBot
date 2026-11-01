"""Pre-order inquiry contract; extracted fields remain unconfirmed candidates."""
from dataclasses import dataclass, field
from core.models import new_id, utc_now

ORDER_FIELDS = ("order_no", "buyer", "destination_country", "product_code", "description",
                "quantity", "unit_price", "currency", "incoterm", "payment_terms")

@dataclass(slots=True)
class Inquiry:
    inquiry_id: str = field(default_factory=lambda: new_id("inquiry"))
    owner_id: str = ""
    title: str = "新询盘"
    target_language: str = "英文"
    product_resource_id: str | None = None
    messages: list[dict] = field(default_factory=list)
    fields: dict = field(default_factory=dict)
    citations: list[dict] = field(default_factory=list)
    case_id: str | None = None
    revision: int = 0
    created_at: str = field(default_factory=lambda: utc_now().isoformat())

    @property
    def awaiting_reply(self):
        return bool(self.messages and self.messages[-1]["role"] == "user")
