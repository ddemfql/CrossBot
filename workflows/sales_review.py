"""Limited local wording checks; unhit text still requires manual review."""
from core.models import RiskItem
from workflows.content_review import review_content


def review_sales_text(content: str, *, source_text: str = "") -> list[RiskItem]:
    return review_content(content, source_text=source_text)
