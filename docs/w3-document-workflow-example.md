# W3 单证流程示例

本文用一笔虚构订单说明 W3 如何从收单走到人工复核和跨单据校验。示例只描述业务数据，不代表解析器已经实现。

## 示例订单

| 字段 | 值 |
|---|---|
| Case 编号 | `CASE-2026-0042` |
| 客户 | Northwind Trading Ltd. |
| 目的国 | Germany |
| 贸易术语 | FCA Shanghai |
| 币种 | USD |
| 订单号 | `NW-PO-2042` |

本单需要一份商业发票和一份装箱单。业务员上传 `invoice-2042.pdf` 与 `packing-list-2042.xlsx` 后，系统为每份文件保留版本、哈希、上传人和解析状态。

## 提取结果

解析器将字段保存为候选值，并记录来源位置，等待复核：

| 单据 | 字段 | 候选值 | 来源 | 置信度 |
|---|---|---|---|---:|
| 商业发票 | 订单号 | `NW-PO-2042` | 第 1 页 | 0.99 |
| 商业发票 | 总金额 | `USD 12,480.00` | 第 1 页 | 0.97 |
| 商业发票 | 商品数量 | `240` | 第 1 页 | 0.91 |
| 装箱单 | 订单号 | `NW-PO-2042` | `Summary!B2` | 0.99 |
| 装箱单 | 商品数量 | `240` | `Summary!B5` | 0.96 |
| 装箱单 | 毛重 | `1,280 kg` | `Summary!B8` | 0.72 |

复核任务可优先展示低置信度字段。复核人确认毛重为 `1,280 kg`，并确认两份单据的订单号和数量无误；确认值标记为 `human` 来源，原候选值仍保留在审计记录中。

## 校验与状态

1. Case 从 `draft` 创建；必需单据尚未齐全时进入 `missing_documents`。
2. 两份单据上传后进入 `parsing`。解析成功的版本进入 `parsed`，失败时保留错误并允许重新上传。
3. 字段提取完成后创建复核任务，Case 进入 `pending_review`。
4. 复核完成后按已确认字段执行跨单据规则：订单号、商品数量和币种金额需要匹配。
5. 本例订单号和商品数量匹配，规则结果为 `pass`。所有必需规则通过后，Case 可进入 `passed`。

如果装箱单数量被提取为 `240`、发票数量为 `250`，数量规则应返回 `fail`，指出两份单据、字段名和原始值，Case 进入 `cross_check_failed`，等待修正单据或人工处理。系统不能静默改写任一单据的值。

## 记录示意

下面是确认后的字段和跨单据结果的简化 JSON，字段名与 `core.models` 中的契约相对应：

```json
{
  "case_number": "CASE-2026-0042",
  "status": "passed",
  "documents": [
    {
      "document_type": "commercial_invoice",
      "original_filename": "invoice-2042.pdf",
      "fields": {
        "order_no": {"value": "NW-PO-2042", "status": "confirmed", "source": "human"},
        "quantity": {"value": 240, "status": "confirmed", "source": "human"},
        "currency": {"value": "USD", "status": "confirmed", "source": "human"}
      }
    },
    {
      "document_type": "packing_list",
      "original_filename": "packing-list-2042.xlsx",
      "fields": {
        "order_no": {"value": "NW-PO-2042", "status": "confirmed", "source": "human"},
        "quantity": {"value": 240, "status": "confirmed", "source": "human"},
        "gross_weight_kg": {"value": 1280, "status": "confirmed", "source": "human"}
      }
    }
  ],
  "cross_checks": [
    {"rule_code": "ORDER_NO_MATCH", "status": "pass"},
    {"rule_code": "QUANTITY_MATCH", "status": "pass"},
    {"rule_code": "CURRENCY_MATCH", "status": "pass"}
  ]
}
```

实际落库时，单据版本、字段来源位置、复核决定和校验结果应分别保存，便于追溯每个最终值的来源。

