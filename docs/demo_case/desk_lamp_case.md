# 特殊案例演示方案：智能灯具出口单证协同核验

## 1. 案例选择

建议使用“USB 多档色温桌面台灯出口德国”的单一订单作为完整演示案例。

案例同时覆盖：

- W1：德国客户询盘、认证和数量确认。
- W2：Amazon/TikTok 多平台文案生成与合规检查。
- W3：商业发票、装箱单和合同的解析、人工复核、交叉比对。
- 知识库：产品参数、德国市场沟通、CE 适用性提示、平台禁用词。
- 贸易数据：使用 BACI 数据生成市场背景，不作为订单事实。

## 2. 固定演示订单

```text
案例编号：CASE-DE-LAMP-001
产品：USB 供电桌面台灯
材质：ABS
色温：三档
目的国：Germany
贸易术语：DAP Frankfurt
币种：EUR
订单数量：500 pcs
单价：18.50 EUR
发票金额：9250.00 EUR
箱数：25 cartons
每箱数量：20 pcs
毛重：185 kg
净重：150 kg
```

以上数据必须进入结构化 Case 和单据样例。模型只能引用已确认字段，不能自行增加证书编号、功率、尺寸、交期或认证结论。

## 3. W1 演示流程

1. 模拟德国客户询问 500 台台灯、目的地、CE 资料和交期。
2. W1 从产品知识库读取已确认参数。
3. API 生成德文或英文商务回复。
4. 本地规则检查认证表达、绝对化承诺、交期和价格承诺。
5. UI 展示客户回复、风险和待确认项。
6. 业务员输入“补充交期和包装信息”，API 生成第二轮回复。
7. 每一轮都重新审查。

## 4. W2 演示流程

1. 选择 Amazon 和 TikTok。
2. 输入已确认产品资料。
3. 分别生成严谨版和短视频版 Listing。
4. 检查“护眼、无风险、100%”等医疗或绝对化表达。
5. 发现风险时标记并要求人工修改。

## 5. W3 演示文件

建议准备 4 份脱敏文件：

```text
invoice_case_de_lamp_001.pdf
packing_list_case_de_lamp_001.xlsx
sales_contract_case_de_lamp_001.pdf
bill_of_lading_case_de_lamp_001.pdf
```

首轮只要求发票和装箱单进入最小闭环；合同和提单作为扩展展示。

### 正常文件

发票、装箱单和 Case 保持以下字段一致：

- 客户
- 订单号
- 目的国
- 数量
- 箱数
- 毛重
- 净重
- 币种
- 总金额

### 冲突文件

复制正常装箱单生成一个失败样例，只修改一个字段，例如：

```text
装箱单数量：480 pcs
发票数量：500 pcs
```

系统必须输出 `FAIL`，显示两侧值、文档、页码或单元格和规则编号。

### 缺失文件

删除装箱单中的净重字段。系统必须输出 `PENDING_REVIEW`，不能判定通过。

## 6. 知识库分层

```text
knowledge/
├── products/
│   └── desk_lamp.json
├── compliance/
│   ├── eu_ce_review.json
│   └── prohibited_claims.json
├── markets/
│   └── germany_business_style.json
├── platforms/
│   ├── amazon_rules.json
│   └── tiktok_rules.json
├── trade/
│   └── baci_query.py
└── translation/
    └── trade_phrase_index.json
```

每条知识至少保存：

```text
knowledge_id
content
category
source
version
effective_date
market
product_scope
confidence
```

## 7. BACI 数据用途

`trade_i_baci_a_22_2024.csv` 只用于生成市场背景：

- 中国对德国或欧洲的相关 HS 产品贸易概览。
- 产品大类和贸易方向参考。
- 客户模拟中的市场背景。

BACI 数据不用于：

- 证明本次客户真实存在。
- 证明订单金额或数量。
- 证明产品认证。
- 直接生成报价或交期。
- 替代合同、发票和装箱单。

原始 CSV 约 1.52 GB，不能在请求时全量读取。应离线生成按国家、HS 编码和产品的索引。

## 8. 演示成功标准

演示人员必须能够完整展示：

1. 客户询盘进入 W1。
2. API 生成回复，未知事实进入待确认列表。
3. W2 生成两个平台版本并拦截风险表达。
4. W3 创建 Case 并上传单据。
5. PDF/Excel 解析结果带来源位置。
6. 人工确认关键字段。
7. Python 交叉比对正常通过。
8. 修改一个单据字段后显示失败。
9. 删除一个字段后显示待复核。
10. 未通过的 Case 不能关闭。

## 9. 案例边界

该案例用于演示系统闭环，不代表真实法律、认证或海关结论。所有认证和贸易合规内容必须保留人工确认节点，并显示知识来源和更新时间。
