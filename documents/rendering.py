"""Versioned, deterministic seller draft renderer. No external issuer documents."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import escape
from string import Template

from core.errors import CrossBotError, ErrorCode
from core.models import DocumentType, TradeData
from core.ports import RenderedOutput

TITLES = {
    DocumentType.PROFORMA_INVOICE: "PROFORMA INVOICE",
    DocumentType.COMMERCIAL_INVOICE: "COMMERCIAL INVOICE",
    DocumentType.PACKING_LIST: "PACKING LIST",
}
TEMPLATE_VERSION = "1"
TEMPLATE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>$title — $order_no</title>
<style>body{font-family:Arial,sans-serif;color:#172b3a;margin:32px;line-height:1.5}
h1{font-size:25px;margin-bottom:4px}.draft{color:#9b4a00;border:1px solid;padding:8px}
table{border-collapse:collapse;width:100%;margin:20px 0}th,td{border:1px solid #bbc7ce;padding:8px;text-align:left}
th{background:#eef3f6}.meta{display:grid;grid-template-columns:1fr 1fr;gap:5px 24px}
footer{font-size:12px;color:#536878;margin-top:28px}@media print{body{margin:15mm}}</style></head>
<body><h1>$title</h1><p class="draft">DRAFT / 草稿 — Human review required before business use.</p>
<div class="meta"><div>Order / 订单: $order_no</div><div>Data revision / 数据版本: $revision</div>
<div>Seller / 卖方: $seller</div><div>Buyer / 买方: $buyer</div>
<div>Destination / 目的地: $destination</div><div>Currency / 币种: $currency</div>
<div>Incoterm / 贸易条款: $incoterm</div><div>Payment / 付款条件: $payment</div>
<div>Transport / 运输方式: $transport</div><div>Expected shipment / 预计出运: $ship_date</div></div>
<table><thead><tr>$headers</tr></thead><tbody>$rows</tbody></table>
<p>$totals</p><footer>Seller-controlled draft. No signature, carrier issuance, customs filing or certification is implied.
Template: $template_id / $template_version. Approval records are maintained separately.</footer></body></html>""")


def _decimal(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money(value: Decimal | None) -> str:
    if value is None:
        return "待补充 / Pending"
    try:
        return format(value, "f")
    except InvalidOperation:
        return "待补充 / Pending"


class HTMLRenderer:
    def render(self, document_type: DocumentType, trade: TradeData) -> RenderedOutput:
        if document_type not in TITLES:
            raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED,
                                "仅支持生成 PI、商业发票和装箱单卖方草稿。")
        packing = document_type is DocumentType.PACKING_LIST
        headers = (["Product", "Description", "Quantity", "Unit", "Packages", "Gross kg", "Net kg"]
                   if packing else ["Product", "Description", "Quantity", "Unit", "Unit price", "Amount"])
        rows, amounts = [], []
        for line in trade.line_items:
            cells = [line.product_code, line.description, line.quantity, line.unit]
            quantity, price = _decimal(line.quantity), _decimal(line.unit_price)
            amount = quantity * price if quantity is not None and price is not None else None
            if amount is not None:
                amounts.append(amount)
            if packing:
                cells += [line.packages, line.gross_weight_kg, line.net_weight_kg]
            else:
                cells += [line.unit_price, _money(amount)]
            rows.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in cells) + "</tr>")
        if packing:
            packages = sum(line.packages for line in trade.line_items
                           if isinstance(line.packages, int) and not isinstance(line.packages, bool))
            gross = [_decimal(line.gross_weight_kg) for line in trade.line_items]
            net = [_decimal(line.net_weight_kg) for line in trade.line_items]
            gross_text = str(sum(gross, Decimal(0))) if all(v is not None for v in gross) else "Pending"
            net_text = str(sum(net, Decimal(0))) if all(v is not None for v in net) else "Pending"
            totals = f"Packages: {packages}; Gross kg: {gross_text}; Net kg: {net_text}"
        else:
            # Preserve exact Decimal totals shared with validation; no hidden rounding.
            total = sum(amounts, Decimal(0))
            totals = (f"Total: {trade.currency} {_money(total)}" if len(amounts) == len(trade.line_items)
                      else "Total: Pending")
        template_id = "w3-" + document_type.value
        substitutions = dict(title=TITLES[document_type], order_no=trade.order_no,
            revision=trade.revision, seller=trade.seller, buyer=trade.buyer,
            destination=trade.destination_country, currency=trade.currency,
            incoterm=f"{trade.incoterm} (Incoterms {trade.incoterm_version})",
            payment=trade.payment_terms, transport=trade.transport_mode,
            ship_date=trade.expected_ship_date or "Pending", totals=totals,
            template_id=template_id, template_version=TEMPLATE_VERSION)
        safe = {key: escape(str(value)) for key, value in substitutions.items()}
        safe.update(headers="".join(f"<th>{escape(value)}</th>" for value in headers), rows="\n".join(rows))
        content = TEMPLATE.substitute(safe).encode("utf-8")
        return RenderedOutput(content=content, media_type="text/html", extension="html",
            template_id=template_id, template_version=TEMPLATE_VERSION,
            template_sha256=sha256(TEMPLATE.template.encode("utf-8")).hexdigest())
