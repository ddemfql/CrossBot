"""Planning V3 synthetic fixture; no customer master data or Case persistence."""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from core.errors import ParseError
from core.models import Case
from core.schemas import ListingRequest, SalesRequest

FIXTURE = Path(__file__).parents[1] / "docs/demo_case/case_fixture.json"


def load_fixture():
    try:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1 and data["data_kind"] == "synthetic"
        assert data["case_number"] == "CASE-DE-LAMP-001"
        for key in ("quantity", "cartons", "units_per_carton", "color_temperature_modes"):
            assert type(data[key]) is int and data[key] > 0
        money = [Decimal(data[k]) for k in ("unit_price", "invoice_total", "gross_weight_kg", "net_weight_kg")]
        assert all(x.is_finite() and x > 0 for x in money)
        assert money[0] * data["quantity"] == money[1]
        assert data["cartons"] * data["units_per_carton"] == data["quantity"]
        assert money[2] >= money[3]
    except (OSError, KeyError, TypeError, ValueError, AssertionError, InvalidOperation):
        raise ParseError("德国台灯演示资料缺失或算术不一致，未加载。") from None
    return data


def demo_case() -> Case:
    d = load_fixture()
    return Case(case_id=d["case_number"], case_number=d["case_number"],
                customer_name="", destination_country=d["destination_country"],
                incoterm=d["incoterm"], currency=d["currency"], invoice_total=Decimal(d["invoice_total"]))


def product_text() -> str:
    d = load_fixture()
    return (f"{d['product_id']}\n{d['name']}，{d['material']}材质，{d['color_temperature_modes']}档色温。\n"
            "以上为项目固定模拟参数；功率、尺寸、颜色、证书编号、认证结论和交期待确认。")


def demo_sales_request() -> SalesRequest:
    d = load_fixture()
    return SalesRequest(
        inquiry="We need 500 USB desk lamps for Germany. Please confirm the specifications, certification documents and delivery time.",
        product_info=(product_text() + f"\n模拟Case：{d['case_number']}；订单数量{d['quantity']} pcs；"
                      f"单价{d['unit_price']} EUR；货品金额{d['invoice_total']} EUR；{d['incoterm']}。\n"
                      f"{d['cartons']}箱，每箱{d['units_per_carton']}件；毛重{d['gross_weight_kg']} kg，净重{d['net_weight_kg']} kg。\n"
                      "这些是演示订单设定，不是实际报价或已确认合同；付款和运输费用范围仍待人工确认。"),
        target_language="英文", customer_country="Germany", requested_quantity=d["quantity"])


def demo_listing_request() -> ListingRequest:
    return ListingRequest(product_text(), ["Amazon", "TikTok"], "Germany", "英文")
