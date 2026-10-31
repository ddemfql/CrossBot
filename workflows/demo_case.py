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
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        # Read A's fixture unchanged; USB is supply type, never invented wattage.
        if raw["case_id"] != "CASE-DE-LAMP-001" or raw["status"] != "draft":
            raise ValueError
        product, trade = raw["product"], raw["trade"]
        if (product["power"] != "USB" or trade["incoterm"] != "DAP"
                or trade["customer_country"] != "Germany" or trade["currency"] != "EUR"):
            raise ValueError
        if any(not isinstance(product[key], str) or not product[key].strip() for key in ("name", "material")):
            raise ValueError
        data = {"case_number": raw["case_id"], "product_id": "DEMO-DE-LAMP-001",
                "name": product["name"], "material": product["material"],
                "color_temperature_modes": product["color_temperature_levels"],
                "destination_country": trade["customer_country"], "incoterm": trade["incoterm"] + " Frankfurt",
                "currency": trade["currency"], "quantity": trade["quantity"], "unit_price": trade["unit_price"],
                "invoice_total": trade["invoice_total"], "cartons": trade["packages"], "units_per_carton": 20,
                "gross_weight_kg": trade["gross_weight_kg"], "net_weight_kg": trade["net_weight_kg"]}
        for key in ("quantity", "cartons", "units_per_carton", "color_temperature_modes"):
            if type(data[key]) is not int or data[key] <= 0:
                raise ValueError
        money = [Decimal(data[k]) for k in ("unit_price", "invoice_total", "gross_weight_kg", "net_weight_kg")]
        if (not all(x.is_finite() and x > 0 for x in money)
                or money[0] * data["quantity"] != money[1]
                or data["cartons"] * data["units_per_carton"] != data["quantity"]
                or money[2] < money[3]):
            raise ValueError
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
