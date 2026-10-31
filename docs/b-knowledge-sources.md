# B第四步：结构化模拟知识与来源

2026-10-31｜feature/w1-w2-platform｜此前前三步已由用户确认，本步经批准实施。

## 修改与行为

- 新增knowledge/products.json、platform_rules.json、compliance_rules.json：共7条，2产品、3平台组织提示、1通用人工核对流程、1德国演示流程提示。全部synthetic、非官方，不是供应商或法律/平台证明。
- knowledge/retrieval.py：校验来源、类型、版本、日期和适用范围；只按完整唯一演示SKU检索产品，按显式国家/平台及日期过滤，不进行模糊参数套用。无命中明确unknown；命中也仅reference_only。文件损坏/缺失返回公共错误并阻止本次客户端调用，不能当作无命中。
- knowledge/ui.py：当前输入预览显示来源/版本/日期/条件及未知状态。W1/W2产品字段可输入DEMO-LAMP-001或DEMO-CUP-001，支持大小写；默认演示文案补充ID。未填国家不从询盘猜测。
- sales/listing请求增加knowledge_context，W1每轮修改重新检索；响应的pending_confirmations保存当次文本引用、内容、版本、条件及知识文件SHA快照。历史不随当前输入预览变化；模拟客户端仍不翻译或调用模型。
- tests/test_knowledge.py新增10项、test_knowledge_ui.py新增2项；更新原输入测试以检查增加知识上下文后原字段仍原样保存。

## 公共契约

继续使用core的SalesRequest/ListingRequest、GenerationResponse与公共错误类型，不新增或复制core公共模型。知识条目/上下文是knowledge内部的命名JSON资料，不用匿名列表位置传递业务字段；请求响应仍为core对象。没有跨分支公共接口变更，无需队友同步新契约。

## 验证

团队目录用外层Python执行 `../../.venv/Scripts/python.exe -B -m unittest discover -s tests -v` 共51项通过（旧39+知识10+知识页面2）。覆盖SKU精确/未知/部分/多个、国家与平台不符、过期与未来日期、坏文件及元数据、模型前拦截、W1两轮引用及W2所选来源、预览与历史隔离。py_compile和git diff --check通过；无新依赖、真实模型请求或.env读取。

为加载变更模块重启8502预览，旧会话历史可能重置。旧进程退出时出现Windows异步连接清理异常，新进程另行启动并核对健康；不据此宣称修复其根因。原8501未操作。

## 用户验收

在8502的W1产品信息填写`DEMO-LAMP-001`，展开「知识库来源与适用条件（当前输入预览）」：应见命中模拟参考、来源路径、demo-1、未知生效日期和适用条件。改为`UNKNOWN-001`：应见产品未知待确认，不能补出认证、报价、交期。

W2填写`DEMO-CUP-001`，只选Amazon生成：引用含STYLE-AMAZON而无STYLE-TIKTOK。不填国家时不引用德国提示；显式填写DE/德国且SKU适用时才出现德国演示提示。

## 限制与交接

所有事实仅是自建设定；即使SKU相同，也不判定输入其余规格与参考一致，更不覆盖用户输入。规则文件实际是模拟风格/流程提示，不是最新官方政策；未知生效日期不解释成“当前法规有效”。认证/价格/交期在知识中均无依据，用户输入也仍需人工确认。

仅进行结构化查询和作用域筛选，没有向量检索、外部同步、真实供应商核实、冲突裁决或模型生成后全量事实校验。真实API尚未接入，提示词约束不构成未来模型绝不幻觉的保证。本次只在B工作区，未commit/push/PR。
