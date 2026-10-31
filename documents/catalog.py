"""Seller worksheet catalog. External issuer records are custody-only."""
from core.models import DocumentType as D

DOC_LABELS = {
    D.QUOTATION: "报价单", D.PROFORMA_INVOICE: "形式发票 PI",
    D.SALES_CONTRACT: "销售合同填写稿", D.ORDER_CONFIRMATION: "订单确认书",
    D.PRODUCT_SPEC: "产品规格表", D.PACKAGING_ARTWORK: "包装标签内容表",
    D.PRODUCTION_ORDER: "生产要求单", D.INSPECTION_RECORD: "内部验货记录",
    D.COMMERCIAL_INVOICE: "商业发票 CI", D.PACKING_LIST: "装箱单 PL",
    D.COMBINED_INVOICE_PACKING_LIST: "商业发票及装箱合并表",
    D.BOOKING_REQUEST: "订舱申请", D.SHIPPING_INSTRUCTION: "运输指示",
    D.CUSTOMS_WORKSHEET: "报关资料准备表", D.PAYMENT_NOTICE: "付款通知",
    D.STATEMENT_OF_ACCOUNT: "对账单", D.COMPLIANCE_DRAFT: "合规声明填写稿",
    D.ISF_WORKSHEET: "ISF 资料准备表", D.PURCHASE_ORDER: "买方订单 PO",
    D.BILL_OF_LADING: "承运人提单", D.AIR_WAYBILL: "航空运单",
    D.CERTIFICATE_OF_ORIGIN: "原产地证", D.INSURANCE_POLICY: "保险单",
    D.BANK_RECEIPT: "银行回单", D.TEST_REPORT: "检测报告",
    D.INSPECTION_REPORT: "第三方验货报告", D.COMPLIANCE: "外部合规资料",
    D.ISF_RECEIPT: "外部申报回执", D.PHOTO: "出货照片", D.OTHER: "其他资料",
    D.PURCHASE_CONTRACT: "供应商采购合同",
    D.FORWARDER_RECEIPT: "货代收货凭证（含草稿）",
}
SELLER_TYPES = (D.QUOTATION, D.PROFORMA_INVOICE, D.SALES_CONTRACT, D.ORDER_CONFIRMATION,
    D.PRODUCT_SPEC, D.PACKAGING_ARTWORK, D.PRODUCTION_ORDER, D.INSPECTION_RECORD,
    D.COMMERCIAL_INVOICE, D.PACKING_LIST, D.COMBINED_INVOICE_PACKING_LIST,
    D.BOOKING_REQUEST, D.SHIPPING_INSTRUCTION, D.CUSTOMS_WORKSHEET, D.PAYMENT_NOTICE,
    D.STATEMENT_OF_ACCOUNT, D.COMPLIANCE_DRAFT, D.ISF_WORKSHEET)
EXTERNAL_TYPES = tuple(kind for kind in D if kind not in SELLER_TYPES)
GROUPS = {"seller": SELLER_TYPES, "external": EXTERNAL_TYPES}

_COMMON = {"document_no": "单据编号", "document_date": "单据日期",
    "order_no": "订单号", "seller": "卖方", "buyer": "买方",
    "contact_person": "联系人", "notes": "备注"}
_PAYMENT = {"currency": "币种", "payment_terms": "付款条件",
    "bank_name": "收款银行", "bank_address": "银行地址",
    "beneficiary": "收款人", "bank_account": "收款账号", "swift": "SWIFT/BIC"}
_SHIPPING = {"shipper": "发货人", "consignee": "收货人", "notify_party": "通知方",
    "port_of_loading": "装运港", "port_of_discharge": "卸货港",
    "destination_country": "目的国家/地区", "transport_mode": "运输方式",
    "expected_ship_date": "预计出运日", "incoterm": "贸易术语"}
_PRODUCT_ROWS = {"product_code": "产品编码", "description": "品名/规格",
    "quantity": "数量", "unit": "单位", "unit_price": "单价", "amount": "金额"}
_PACKING_ROWS = {"product_code": "产品编码", "description": "品名/规格",
    "quantity": "数量", "unit": "单位", "packages": "箱数",
    "gross_weight_kg": "毛重 kg", "net_weight_kg": "净重 kg",
    "marks_and_numbers": "唛头/件号", "package_contents": "包装内容"}

_SPECIFIC = {
    D.QUOTATION: {**_PAYMENT, "valid_until": "报价有效期", "incoterm": "贸易术语", "lead_time": "交期"},
    D.PROFORMA_INVOICE: {**_PAYMENT, **_SHIPPING, "invoice_no": "发票号", "invoice_date": "发票日期"},
    D.SALES_CONTRACT: {**_PAYMENT, **_SHIPPING, "contract_no": "合同号", "quality_terms": "质量约定", "delivery_terms": "交付约定", "dispute_terms": "争议解决约定"},
    D.ORDER_CONFIRMATION: {**_PAYMENT, **_SHIPPING, "buyer_po_no": "买方采购单号", "delivery_date": "约定交期"},
    D.PRODUCT_SPEC: {"product_name": "产品名称", "model": "型号", "material": "材质", "dimensions": "尺寸", "technical_requirements": "技术要求", "reference_standard": "用户提供的参考标准"},
    D.PACKAGING_ARTWORK: {"product_name": "产品名称", "packaging_material": "包装材料", "label_language": "标签语言", "label_text": "标签原文", "artwork_reference": "包装稿版本/来源"},
    D.PRODUCTION_ORDER: {"factory": "生产单位", "delivery_date": "要求交期", "quality_requirements": "质量要求", "packaging_requirements": "包装要求"},
    D.INSPECTION_RECORD: {"inspection_date": "实际检查日期", "inspector": "实际检查人", "location": "检查地点", "sampling_plan": "实际抽样方案", "observed_result": "人工记录的检查结果", "evidence_reference": "照片/记录依据"},
    D.COMMERCIAL_INVOICE: {**_PAYMENT, **_SHIPPING, "invoice_no": "发票号", "invoice_date": "发票日期", "country_of_origin": "原产国"},
    D.PACKING_LIST: {**_SHIPPING, "packing_list_no": "装箱单号", "packing_date": "装箱日期"},
    D.COMBINED_INVOICE_PACKING_LIST: {**_PAYMENT, **_SHIPPING, "invoice_no": "发票号", "invoice_date": "发票日期"},
    D.BOOKING_REQUEST: {**_SHIPPING, "forwarder": "货代", "cargo_ready_date": "货好日期", "container_type": "箱型/数量", "cargo_description": "货物说明", "volume_cbm": "体积 m³"},
    D.SHIPPING_INSTRUCTION: {**_SHIPPING, "booking_reference": "外部订舱参考", "freight_terms": "运费条款", "cargo_description": "货物说明"},
    D.CUSTOMS_WORKSHEET: {**_SHIPPING, "broker": "报关代理", "invoice_no": "发票号", "currency": "币种", "declaration_notes": "人工填写的申报准备说明"},
    D.PAYMENT_NOTICE: {**_PAYMENT, "invoice_no": "对应发票号", "amount_due": "应付金额", "due_date": "付款到期日", "payment_reference": "汇款附言"},
    D.STATEMENT_OF_ACCOUNT: {**_PAYMENT, "period_start": "账期开始", "period_end": "账期结束", "opening_balance": "期初余额", "closing_balance": "期末余额"},
    D.COMPLIANCE_DRAFT: {"product_name": "产品名称", "market": "目标市场", "manufacturer": "制造商", "applicable_requirements": "人工核实的适用要求", "evidence_reference": "真实证据文件/版本", "declaration_text": "授权人员填写的声明正文", "responsible_person": "待核实的责任人"},
    D.ISF_WORKSHEET: {**_SHIPPING, "importer": "进口商", "importer_record_no": "进口商记录号", "manufacturer": "制造商", "seller_address": "卖方地址", "buyer_address": "买方地址", "container_stuffing_location": "装箱地点", "consolidator": "拼箱/装箱单位", "bill_reference": "外部提单参考", "country_of_origin": "原产国"},
}
FORM_FIELDS = {kind: {**_COMMON, **_SPECIFIC[kind]} for kind in SELLER_TYPES}
ROW_FIELDS = {kind: dict(_PRODUCT_ROWS) for kind in SELLER_TYPES}
for _kind in (D.PACKING_LIST, D.PACKAGING_ARTWORK, D.BOOKING_REQUEST, D.SHIPPING_INSTRUCTION):
    ROW_FIELDS[_kind] = dict(_PACKING_ROWS)
for _kind in (D.CUSTOMS_WORKSHEET, D.ISF_WORKSHEET, D.COMBINED_INVOICE_PACKING_LIST):
    ROW_FIELDS[_kind] = {**_PRODUCT_ROWS, **_PACKING_ROWS, "hs_code": "HS/HTS 编码", "country_of_origin": "原产国"}
ROW_FIELDS[D.PRODUCT_SPEC] = {"parameter": "参数名称", "value": "参数值", "unit": "单位", "basis": "依据"}
ROW_FIELDS[D.INSPECTION_RECORD] = {"check_item": "检查项目", "requirement": "要求", "actual": "实测/观察值", "result": "人工判定", "evidence": "证据"}
ROW_FIELDS[D.COMPLIANCE_DRAFT] = {"requirement": "已核实要求", "evidence": "真实证据", "status": "人工核实情况"}
ROW_FIELDS[D.STATEMENT_OF_ACCOUNT] = {"date": "日期", "reference": "发票/收款参考", "debit": "应收", "credit": "实收", "balance": "余额"}

# Practical per-document minimums for our drafts, not a legal sufficiency claim.
REQUIRED_FIELDS = {
    D.QUOTATION: ("document_no", "document_date", "seller", "buyer", "currency"),
    D.PROFORMA_INVOICE: ("document_no", "document_date", "order_no", "seller", "buyer", "currency"),
    D.SALES_CONTRACT: ("document_no", "document_date", "seller", "buyer", "currency"),
    D.ORDER_CONFIRMATION: ("document_no", "document_date", "order_no", "seller", "buyer"),
    D.PRODUCT_SPEC: ("product_name",),
    D.PACKAGING_ARTWORK: ("product_name", "label_text"),
    D.PRODUCTION_ORDER: ("document_no", "order_no", "factory", "delivery_date", "quality_requirements"),
    D.INSPECTION_RECORD: ("inspection_date", "inspector", "observed_result", "evidence_reference"),
    D.COMMERCIAL_INVOICE: ("document_no", "document_date", "order_no", "seller", "buyer", "currency"),
    D.PACKING_LIST: ("document_no", "document_date", "order_no", "seller", "buyer"),
    D.COMBINED_INVOICE_PACKING_LIST: ("document_no", "document_date", "order_no", "seller", "buyer", "currency"),
    D.BOOKING_REQUEST: ("forwarder", "port_of_loading", "port_of_discharge", "cargo_ready_date", "cargo_description"),
    D.SHIPPING_INSTRUCTION: ("shipper", "consignee", "port_of_loading", "port_of_discharge", "cargo_description"),
    D.CUSTOMS_WORKSHEET: ("broker", "invoice_no", "currency"),
    D.PAYMENT_NOTICE: ("document_no", "document_date", "seller", "buyer", "currency", "amount_due", "due_date", "bank_account"),
    D.STATEMENT_OF_ACCOUNT: ("seller", "buyer", "period_start", "period_end", "currency", "opening_balance", "closing_balance"),
    D.COMPLIANCE_DRAFT: ("product_name", "market", "evidence_reference", "declaration_text", "responsible_person"),
    D.ISF_WORKSHEET: ("importer", "importer_record_no", "manufacturer", "container_stuffing_location", "consolidator"),
}
ROW_REQUIRED_TYPES = frozenset({D.QUOTATION, D.PROFORMA_INVOICE, D.SALES_CONTRACT,
    D.ORDER_CONFIRMATION, D.PRODUCTION_ORDER, D.COMMERCIAL_INVOICE, D.PACKING_LIST,
    D.COMBINED_INVOICE_PACKING_LIST, D.CUSTOMS_WORKSHEET, D.STATEMENT_OF_ACCOUNT})
REQUIRED_ROW_FIELDS = {kind: ("description", "quantity", "unit") for kind in SELLER_TYPES}
for _kind in (D.QUOTATION, D.PROFORMA_INVOICE, D.SALES_CONTRACT, D.ORDER_CONFIRMATION,
              D.COMMERCIAL_INVOICE, D.COMBINED_INVOICE_PACKING_LIST):
    REQUIRED_ROW_FIELDS[_kind] += ("unit_price",)
REQUIRED_ROW_FIELDS[D.PRODUCT_SPEC] = ("parameter", "value")
REQUIRED_ROW_FIELDS[D.INSPECTION_RECORD] = ("check_item", "actual", "result")
REQUIRED_ROW_FIELDS[D.COMPLIANCE_DRAFT] = ("requirement", "evidence", "status")
REQUIRED_ROW_FIELDS[D.STATEMENT_OF_ACCOUNT] = ("date", "reference", "balance")
REQUIRED_ROW_FIELDS[D.PAYMENT_NOTICE] = ()
REQUIRED_ROW_FIELDS[D.ISF_WORKSHEET] = ("description",)
for _packing_kind in (D.PACKING_LIST, D.COMBINED_INVOICE_PACKING_LIST):
    REQUIRED_ROW_FIELDS[_packing_kind] += ("packages", "gross_weight_kg", "net_weight_kg")

# New documents start with a small, useful schema. Full catalogs above remain
# available for explicit customization and for restoring historical snapshots.
DEFAULT_FIELDS = {
    D.QUOTATION: ["document_no", "document_date", "seller", "buyer", "currency", "valid_until", "incoterm", "lead_time", "payment_terms"],
    D.PROFORMA_INVOICE: ["invoice_no", "invoice_date", "order_no", "seller", "buyer", "currency", "incoterm", "payment_terms", "destination_country"],
    D.SALES_CONTRACT: ["contract_no", "document_date", "seller", "buyer", "currency", "incoterm", "payment_terms", "delivery_terms", "quality_terms"],
    D.ORDER_CONFIRMATION: ["document_no", "document_date", "order_no", "seller", "buyer", "buyer_po_no", "delivery_date", "payment_terms"],
    D.PRODUCT_SPEC: ["product_name", "model", "material", "dimensions", "technical_requirements", "reference_standard"],
    D.PACKAGING_ARTWORK: ["product_name", "packaging_material", "label_language", "label_text", "artwork_reference"],
    D.PRODUCTION_ORDER: ["document_no", "order_no", "factory", "delivery_date", "quality_requirements", "packaging_requirements"],
    D.INSPECTION_RECORD: ["inspection_date", "inspector", "location", "sampling_plan", "observed_result", "evidence_reference"],
    D.COMMERCIAL_INVOICE: ["invoice_no", "invoice_date", "order_no", "seller", "buyer", "currency", "incoterm", "payment_terms", "destination_country"],
    D.PACKING_LIST: ["packing_list_no", "packing_date", "order_no", "seller", "buyer", "shipper", "consignee", "destination_country"],
    D.COMBINED_INVOICE_PACKING_LIST: ["invoice_no", "invoice_date", "order_no", "seller", "buyer", "currency", "shipper", "consignee", "destination_country"],
    D.BOOKING_REQUEST: ["forwarder", "shipper", "consignee", "port_of_loading", "port_of_discharge", "cargo_ready_date", "container_type", "cargo_description", "volume_cbm"],
    D.SHIPPING_INSTRUCTION: ["shipper", "consignee", "notify_party", "port_of_loading", "port_of_discharge", "booking_reference", "freight_terms", "cargo_description"],
    D.CUSTOMS_WORKSHEET: ["broker", "invoice_no", "currency", "shipper", "consignee", "destination_country", "declaration_notes"],
    D.PAYMENT_NOTICE: ["document_no", "document_date", "seller", "buyer", "currency", "invoice_no", "amount_due", "due_date", "bank_name", "beneficiary", "bank_account", "swift", "payment_reference"],
    D.STATEMENT_OF_ACCOUNT: ["seller", "buyer", "currency", "period_start", "period_end", "opening_balance", "closing_balance"],
    D.COMPLIANCE_DRAFT: ["product_name", "market", "manufacturer", "applicable_requirements", "evidence_reference", "declaration_text", "responsible_person"],
    D.ISF_WORKSHEET: ["importer", "importer_record_no", "manufacturer", "seller", "seller_address", "buyer", "buyer_address", "container_stuffing_location", "consolidator", "bill_reference", "country_of_origin"],
}
DEFAULT_ROW_FIELDS = {
    D.QUOTATION: ["product_code", "description", "quantity", "unit", "unit_price", "amount"],
    D.PROFORMA_INVOICE: ["product_code", "description", "quantity", "unit", "unit_price", "amount"],
    D.SALES_CONTRACT: ["product_code", "description", "quantity", "unit", "unit_price", "amount"],
    D.ORDER_CONFIRMATION: ["product_code", "description", "quantity", "unit", "unit_price", "amount"],
    D.PRODUCT_SPEC: ["parameter", "value", "unit", "basis"],
    D.PACKAGING_ARTWORK: [],
    D.PRODUCTION_ORDER: ["product_code", "description", "quantity", "unit"],
    D.INSPECTION_RECORD: ["check_item", "requirement", "actual", "result", "evidence"],
    D.COMMERCIAL_INVOICE: ["product_code", "description", "quantity", "unit", "unit_price", "amount"],
    D.PACKING_LIST: ["product_code", "description", "quantity", "unit", "packages", "gross_weight_kg", "net_weight_kg", "marks_and_numbers", "package_contents"],
    D.COMBINED_INVOICE_PACKING_LIST: list(ROW_FIELDS[D.COMBINED_INVOICE_PACKING_LIST]),
    D.BOOKING_REQUEST: [], D.SHIPPING_INSTRUCTION: [],
    D.CUSTOMS_WORKSHEET: ["product_code", "description", "quantity", "unit", "unit_price", "amount", "hs_code", "country_of_origin"],
    D.PAYMENT_NOTICE: [],
    D.STATEMENT_OF_ACCOUNT: list(ROW_FIELDS[D.STATEMENT_OF_ACCOUNT]),
    D.COMPLIANCE_DRAFT: [],
    D.ISF_WORKSHEET: ["product_code", "description", "hs_code", "country_of_origin"],
}
DEFAULT_CONTEXT_FIELDS = {kind: [] for kind in SELLER_TYPES}
DEFAULT_CONTEXT_FIELDS[D.PACKING_LIST] = ["packages"]
DEFAULT_CONTEXT_FIELDS[D.COMBINED_INVOICE_PACKING_LIST] = ["packages"]

# These are human annotations of supplied records, never issuer document templates.
for _kind in EXTERNAL_TYPES:
    FORM_FIELDS[_kind] = {**_COMMON, "issuer": "原件记载的签发方", "external_reference": "原件单号/证书号",
        "issued_on": "原件签发日期", "expires_on": "原件有效期", "source_party": "资料来源方",
        "shipper": "原件发货人", "consignee": "原件收货人", "currency": "原件币种",
        "total_amount": "原件金额", "model": "原件产品型号", "standard": "原件记载的标准",
        "recorded_conclusion": "原件结论（人工抄录）", "evidence_reference": "原件及版本依据"}
    ROW_FIELDS[_kind] = {"item": "原件项目", "value": "人工抄录值", "source_location": "原件页码/位置"}

FORM_FIELDS[D.PURCHASE_CONTRACT].update({"supplier": "供应商", "buyer": "采购方",
    "contract_no": "采购合同号", "currency": "原件币种", "total_amount": "原件合同金额",
    "payment_terms": "原件付款条件", "delivery_date": "原件交付日期"})
# Customer order data may be supplied directly by a human without inventing a
# buyer-issued original. These fields are data records, not an issuer template.
FORM_FIELDS[D.PURCHASE_ORDER].update({**_PAYMENT, **_SHIPPING})
ROW_FIELDS[D.PURCHASE_ORDER] = {**_PRODUCT_ROWS, **_PACKING_ROWS}
FORM_FIELDS[D.FORWARDER_RECEIPT].update({"vessel": "原件船名", "voyage": "原件航次",
    "container_no": "原件集装箱号", "seal_no": "原件封条号", "volume_cbm": "原件体积 m³",
    "packages": "原件件数", "gross_weight_kg": "原件毛重 kg",
    "reported_received_date": "原件记载的收货日期", "document_status": "原件状态（含草稿标记）"})
