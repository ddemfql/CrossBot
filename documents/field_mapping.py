"""Conservative label-to-value extraction shared by W3 text and OCR parsers."""
from __future__ import annotations

import re
from collections import OrderedDict

from core.errors import ParseError
from core.models import ExtractedField, FieldValueStatus

_LABELS = {
    "order_no": ("order no", "order number", "po no", "po number", "purchase order no",
                 "purchase order number", "订单号", "订单编号", "采购订单号"),
    "customer_name": ("customer", "buyer", "customer name", "buyer name", "bill to",
                      "买方", "客户", "客户名称"),
    "destination_country": ("destination country", "country of destination", "ship to country",
                             "目的国", "目的地国家"),
    "product_name": ("product", "product name", "product description", "description", "item description", "品名", "产品名称"),
    "quantity": ("quantity", "qty", "order qty", "数量"),
    "unit": ("unit", "uom", "单位"),
    "unit_price": ("unit price", "price per unit", "单价"),
    "currency": ("currency", "币种", "货币"),
    "total_amount": ("total amount", "invoice total", "grand total", "total value", "总金额", "发票金额"),
    "incoterm": ("incoterm", "trade term", "terms of delivery", "贸易术语", "贸易条款"),
    "packages": ("packages", "package count", "cartons", "箱数", "件数"),
    "gross_weight_kg": ("gross weight", "gross wt", "毛重"),
    "net_weight_kg": ("net weight", "net wt", "净重"),
    "shipper": ("shipper", "exporter", "发货人"),
    "consignee": ("consignee", "收货人"),
    "country_of_origin": ("country of origin", "origin country", "原产国"),
    "hs_code": ("hs code", "htsus", "harmonized code", "海关编码"),
    "grade_quality": ("grade", "quality", "grade or quality", "等级", "质量"),
    "marks_and_numbers": ("marks and numbers", "shipping marks", "唛头", "箱号"),
    "package_contents": ("package contents", "contents per package", "包装内容"),
    "expected_ship_date": ("shipment date", "ship date", "delivery date", "出货日期", "交货日期"),
}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value.casefold())


_LOOKUP = {_normalize(alias): name for name, aliases in _LABELS.items()
           for alias in (name, *aliases)}
_LOOKUP["seller"] = "shipper"
_LOOKUP["supplier"] = "shipper"


def _key_value(text: str, lookup=None):
    """Return a recognized field and non-empty value from an explicit label row."""
    lookup = _LOOKUP if lookup is None else lookup
    parts = re.split(r"\s*[:：=]\s*|\t+|\s{2,}", text.strip(), maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        return None
    key = _normalize(parts[0].strip(" .#-"))
    name = lookup.get(key)
    if not name:
        return None
    value = parts[1].strip(" \t:：=;；")
    if not value or _normalize(value) in lookup:
        return None
    return name, value


def _field(name: str, value: str, *, page_number=None, confidence=None, note=""):
    return ExtractedField(name=name, value=value, normalized_value=value,
        status=FieldValueStatus.CANDIDATE, confidence=confidence,
        page_number=page_number, note=note or "标签匹配得到机器候选；需人工复核。")


def from_text_lines(lines: list[tuple[str, int | None]]) -> list[ExtractedField]:
    """Parse explicit label/value rows. Repeated matching values are deduplicated."""
    found: OrderedDict[str, ExtractedField] = OrderedDict()
    for text, page in lines:
        parsed = _key_value(text)
        if not parsed:
            continue
        name, value = parsed
        previous = found.get(name)
        if previous is not None:
            if previous.value != value:
                raise ParseError("固定标签存在互相冲突的值，请保留原件并人工复核。")
            continue
        found[name] = _field(name, value, page_number=page)
    return list(found.values())


def from_ocr_lines(lines, *, lookup=None) -> list[ExtractedField]:
    """Parse OCR label rows and simple label/value pairs adjacent on one visual row."""
    lookup = _LOOKUP if lookup is None else lookup
    ordered = sorted(lines, key=lambda item: (item.page_number or 0, item.bbox[1], item.bbox[0]))
    found: OrderedDict[str, ExtractedField] = OrderedDict()
    for index, item in enumerate(ordered):
        parsed = _key_value(item.text, lookup)
        if parsed:
            name, value = parsed
        else:
            label = lookup.get(_normalize(item.text.strip(" .#-:")))
            if not label:
                continue
            height = max(1.0, item.bbox[3] - item.bbox[1])
            center_y = (item.bbox[1] + item.bbox[3]) / 2
            neighbors = []
            for candidate in ordered[index + 1:]:
                if candidate.page_number != item.page_number:
                    if (candidate.page_number or 0) > (item.page_number or 0):
                        break
                    continue
                cy = (candidate.bbox[1] + candidate.bbox[3]) / 2
                if candidate.bbox[0] < item.bbox[2] or abs(cy - center_y) > max(8, height * 0.85):
                    continue
                value = candidate.text.strip()
                if _normalize(value.strip(" .#-:")) in lookup:
                    continue
                neighbors.append((candidate.bbox[0], candidate, value))
            if not neighbors:
                continue
            _, candidate, value = min(neighbors, key=lambda row: row[0])
            name = label
            confidence = (min(item.confidence, candidate.confidence)
                if item.confidence is not None and candidate.confidence is not None else None)
            score = f"{confidence:.2f}" if confidence is not None else "not provided"
            note = (f"OCR candidate; page {item.page_number}; "
                    f"confidence {score}; approximate image bounds retained by OCR.")
            field = _field(name, value, page_number=item.page_number,
                           confidence=confidence, note=note)
            previous = found.get(name)
            if previous and previous.value != value:
                raise ParseError("OCR 对同一标签识别出多个不同值，请人工复核原件。")
            found.setdefault(name, field)
            continue
        confidence = item.confidence
        score = f"{confidence:.2f}" if confidence is not None else "not provided"
        note = (f"OCR candidate; page {item.page_number}; confidence {score}; "
                "需人工复核原件。")
        field = _field(name, value, page_number=item.page_number,
                       confidence=confidence, note=note)
        previous = found.get(name)
        if previous and previous.value != value:
            raise ParseError("OCR 对同一标签识别出多个不同值，请人工复核原件。")
        found.setdefault(name, field)
    return list(found.values())
