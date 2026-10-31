# 第四步知识资料：全部为模拟

本目录7条资料为项目自建设定：2款产品、3条平台组织提示、1条通用人工核对流程、1条德国演示流程提示。不是供应商证明、法律意见或平台官方规则；没有抓取真实客户资料，也没有验证最新政策。

每条包含id/type/content/facts、source、version、effective_from/effective_until及产品/平台/国家适用列表。生效/截止未知时用null；列表为空表示不按该维度限制。source.kind=synthetic、official=false。所有认证、价格、交期均未给出知识依据，禁止据此补写。

产品须在产品信息中明确写完整DEMO-LAMP-001或DEMO-CUP-001，大小写不敏感；通用“台灯”“咖啡杯”等不算产品身份匹配。未知SKU、多个不同SKU、过期/尚未生效或条件不符时不使用该产品资料。即使ID命中，也不判断其他文字是否与目录一致，必须人工核对。

平台仅返回所选平台提示；德国提示只在显式DE/Germany/德国且演示SKU适用时出现，未填国家不推断。该提示不是德国法律要求。检索结果始终标注模拟reference_only，永远不是已验证产品或合规通过。

文件缺失/格式错误返回明确错误并阻止本次生成，不能伪装成无命中。命中上下文进入W1首版/每轮修改及W2请求，结果保存文本引用、版本、条件和知识文件SHA快照；UI预览仅对应当前输入。尚未实现向量检索、外部知识更新、冲突裁决或正式知识契约扩展。

## V3 固定案例增量

demo_case_products.json 新增 DEMO-DE-LAMP-001，一条模拟产品知识；资料总数由7条增为8条。价格只在固定Case演示订单中存在，不写为产品通用报价。原 products/platform_rules/compliance_rules 三文件保留字节不变，历史引用与SHA仍可核对。该文件不是经账号审核发布的正式产品主档，知识治理依赖A的版本与权限契约。

## 动物台灯固定演示增量

`animal_lamp_knowledge.json`、`docs/demo_case/animal_lamp_fixture.json` 和 `workflows/animal_lamp_demo.py` 提供隔离的自编案例 `DEMO-ANIMAL-LAMP-001` / `CASE-DE-ANIMAL-LAMP-001`。不修改早期德国台灯 SKU、Case 或它们的历史引用。数据标记 `synthetic`，虚构供应商为 CrossBot Demo Lighting。

固定交易为 500 件、20 件/箱、25 箱、EUR 18.50/件、EUR 9,250 货品总额、EXW Hangzhou、订单确认及收到约定款项后 25 个工作日生产。按单件 0.4 kg、每箱净重 8 kg、毛重 10 kg 计算，总净重 200 kg、总毛重 250 kg。fixture loader 会以 Decimal 拒绝金额、箱数或重量不一致；认证、证书编号、德国注册信息、续航和送达日期保持未知。

`prohibited_terms.json` 是可编辑的项目演示词库，10 条规则覆盖认证、绝对化、医疗功效、护眼、性能推断、交期、续航、儿童安全、健康/光学声明和注册声明。W1/W2 共用 `workflows.content_review.review_content`，逐条匹配并返回规则 ID、版本和改写建议；不自动删除或批准。规则文件会被校验并参与知识快照 SHA256，缺失/无效时生成失败关闭。此词库不是官方平台规则或完整违禁词集合。新 SKU 对应五条模拟知识，当前条目合计 13。
