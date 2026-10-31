"""Independent, self-consistent synthetic W1/W2 fixture; legacy Case stays intact."""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from core.errors import ParseError
from core.models import Case, DocumentType
from core.schemas import ListingRequest, SalesRequest
from knowledge.retrieval import load_entries

FIXTURE = Path(__file__).parents[1] / "docs/demo_case/animal_lamp_fixture.json"
PRODUCT_ID = "DEMO-ANIMAL-LAMP-001"
CASE_ID = "CASE-DE-ANIMAL-LAMP-001"


def load_animal_fixture():
    try:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        if (data["schema_version"] != 1 or data["data_kind"] != "synthetic"
                or data["case_id"] != CASE_ID or data["product_id"] != PRODUCT_ID
                or data["status"] != "draft"):
            raise ValueError
        records, _ = load_entries()
        product = next(r for r in records if r["id"] == PRODUCT_ID)
        if product["type"] != "product" or product["version"] != data["version"]:
            raise ValueError
        facts, trade = product["facts"], data["trade"]
        for key in ("customer_name", "order_no", "version", "source"):
            if not isinstance(data[key], str) or not data[key].strip():
                raise ValueError
        for key in ("name", "supplier", "model", "material", "shape", "power_supply", "connector"):
            if not isinstance(facts[key], str) or not facts[key].strip():
                raise ValueError
        if (facts["model"] != PRODUCT_ID or trade["currency"] != "EUR"
                or trade["customer_country"] != "Germany" or trade["incoterm"] != "EXW Hangzhou"
                or trade["freight_included"] is not False or trade["import_tax_included"] is not False
                or not isinstance(trade["lead_time_start"], str) or not trade["lead_time_start"].strip()):
            raise ValueError
        for value in [trade["quantity"], trade["packages"], trade["production_lead_time_business_days"],
                      facts["units_per_carton"], facts["battery_capacity_mah"], facts["input_voltage_v"], facts["power_w"]]:
            if type(value) is not int or value <= 0:
                raise ValueError
        for key in ("color_temperatures_k", "dimensions_cm", "carton_dimensions_cm"):
            values = facts[key]
            if not isinstance(values, list) or len(values) != 3 or any(type(v) is not int or v <= 0 for v in values):
                raise ValueError
        def positive(value):
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError
            result = Decimal(value)
            if not result.is_finite() or result <= 0:
                raise ValueError
            return result
        unit_price, total, net, gross = [positive(trade[k]) for k in ("unit_price", "invoice_total", "net_weight_kg", "gross_weight_kg")]
        item_net, carton_net, carton_gross = [positive(facts[k]) for k in ("unit_net_weight_kg", "carton_net_weight_kg", "carton_gross_weight_kg")]
        if (unit_price * trade["quantity"] != total
                or trade["packages"] * facts["units_per_carton"] != trade["quantity"]
                or item_net * facts["units_per_carton"] != carton_net
                or carton_net * trade["packages"] != net
                or carton_gross * trade["packages"] != gross or carton_gross < carton_net):
            raise ValueError
        for key in ("certifications", "certificate_number", "epr_registration", "runtime_hours",
                    "charging_time_hours", "waterproof_rating", "age_suitability", "price", "lead_time"):
            if facts[key] is not None:
                raise ValueError
        if trade["payment_terms"] is not None or trade["arrival_date"] is not None:
            raise ValueError
        script = data["script"]
        if not isinstance(script["feedback"], list) or len(script["feedback"]) != 2:
            raise ValueError
        for value in [script["inquiry"], script["risk_sample"], script["safe_sample"], *script["feedback"]]:
            if not isinstance(value, str) or not value.strip():
                raise ValueError
        [DocumentType(name) for name in data["documents_required"]]
    except (OSError, KeyError, TypeError, ValueError, InvalidOperation, StopIteration):
        raise ParseError("动物台灯演示资料缺失、版本不匹配或金额/装箱/重量不一致，未加载。") from None
    return data, product


def _product_text(product):
    f = product["facts"]
    dimensions = " × ".join(str(v) + " cm" for v in f["dimensions_cm"])
    carton_size = " × ".join(str(v) + " cm" for v in f["carton_dimensions_cm"])
    temperatures = " / ".join(str(v) + " K" for v in f["color_temperatures_k"])
    return (f"{PRODUCT_ID}\n【自编演示数据】供应商：{f['supplier']}；非真实商品或认证证明。\n"
            f"产品：{f['name']}；{f['material']}，{f['shape']}；{f['power_supply']}，{f['connector']}接口。\n"
            f"电池容量 {f['battery_capacity_mah']} mAh；输入 DC {f['input_voltage_v']} V；LED功率 {f['power_w']} W。\n"
            f"三档色温 {temperatures}；尺寸 {dimensions}；单件净重 {f['unit_net_weight_kg']} kg。\n"
            f"包装 {f['units_per_carton']} pcs/箱；外箱 {carton_size}；每箱净重 {f['carton_net_weight_kg']} kg、毛重 {f['carton_gross_weight_kg']} kg。\n"
            "认证文件、证书编号、EPR/WEEE/LUCID注册、续航时长、充电时间、防水等级和适用年龄均待确认；不得编写护眼或健康功效。")


def animal_sales_request():
    data, product = load_animal_fixture()
    t = data["trade"]
    return SalesRequest(
        inquiry=data["script"]["inquiry"],
        product_info=(_product_text(product) + f"\n演示订单 {data['case_id']}：{t['quantity']} pcs；单价 {t['unit_price']} EUR；"
                      f"货品总金额 {t['invoice_total']} EUR；{t['incoterm']}，不含运输费及进口税费。\n"
                      f"合计 {t['packages']} 箱；总净重 {t['net_weight_kg']} kg；总毛重 {t['gross_weight_kg']} kg。\n"
                      f"生产周期 {t['production_lead_time_business_days']} business days（工作日），{t['lead_time_start']}起算；不含运输时间。"
                      "付款比例、具体送达日期待确认。报价与生产周期仅属于该演示订单，不是通用产品承诺。"),
        target_language="英文", customer_country="Germany", requested_quantity=t["quantity"],
    )


def animal_listing_request():
    _, product = load_animal_fixture()
    return ListingRequest(_product_text(product), ["Amazon", "TikTok"], "Germany", "英文")


def animal_lamp_case():
    data, product = load_animal_fixture()
    t = data["trade"]
    return Case(case_id=CASE_ID, case_number=CASE_ID, customer_name=data["customer_name"],
                order_no=data["order_no"], destination_country=t["customer_country"], incoterm=t["incoterm"],
                currency=t["currency"], invoice_total=Decimal(t["invoice_total"]),
                required_document_types=[DocumentType(name) for name in data["documents_required"]],
                metadata={"is_demo": True, "demo_scenario": "animal_lamp", "fixture": data,
                          "product_snapshot": product, "source": "docs/demo_case/animal_lamp_fixture.json"})
