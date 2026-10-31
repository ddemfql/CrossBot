# B阶段性交接：W1/W2、客户端、模拟知识与导出

2026-10-31｜洪彬斌｜feature/w1-w2-platform｜基线722e56a（A公共契约）。

## 问题与结果

旧页面通过固定换行拆业务字段，W1输入换行后可能串位，W2所选平台未进入旧模型请求，反馈容易走错首次路径。当前页面仅编排W1/W2新面板，使用结构化请求、明确修订状态和严格平台集合；默认模拟，真实调用由用户手动触发，成功后保存业务快照并可下载。

## 修改范围

- app.py、README.md：新面板路由、模式设置/导出入口与准确启动说明。
- workflows/：sales、listing、conversation、有限sales_review、两页面、业务exports/export_ui。
- providers/：ChatClient协议、两个明确模拟客户端、统一HTTPS客户端与本机会话设置。
- knowledge/：7条自建模拟资料、检索/校验/来源展示及说明。
- tests/：W1传递/会话/页面、W2平台/页面、知识/来源/页面、HTTP/模式界面、导出测试。
- docs/：各步交接、原样真实业务导出及校验。未包含个人memory、Key、.env、虚拟环境或本机配置。

## 公共契约与整合

复用core.schemas.SalesRequest/ListingRequest/GenerationResponse、core.errors及core.config.AppConfig；核心模型、A的storage、C/D业务文件不变。不需其他分支同步新core契约。知识内部为命名JSON资料，跨业务请求响应仍是core对象。

未来流式输出使用core.events；本阶段同步返回，不自建特殊字符串标记。新W1入口generate_sales/start_sales/revise_sales，新W2入口generate_listings；真实client实现complete(messages)。其他成员新增页面时可延续app.py薄路由，在集成分支接入各自模块，不修改本分支以外的代码。

本草稿PR暂以feature/foundation-contracts为审阅比较基线（integration/phase-1尚未建立），只供团队查看差异；不自动将B实现合入A分支，集成目标由队长决定。

## 测试、样例与失败场景

运行`python -B -m unittest discover -s tests -v`。本机使用外层.venv复验全部69项通过；相关语法和diff检查通过。测试无真实模型网络，不读取.env，未新增依赖；全新电脑安装未实测。

W1输入可多行，API请求仍按命名字段完整传递；缺必填/非法数量在调用前拒绝。每次修订携带原请求、上次文本和本轮反馈，重新查知识并筛查有限声明词；失败不追加，输入变化阻止沿旧会话修改。

W2空/重复/未知平台在调用前拒绝；返回多平台、少平台、空值、非法或重复键JSON不落新结果；输入变化隐藏旧结果。HTTP认证/余额/限流/超时/网络/空及截断响应静态报错，不落正常草稿，不自动重试。

知识资料缺失/损坏报错而非假装没命中；普通产品名、未知/多个SKU、条件或日期不符不套参数。命中仅为reference_only。导出只白名单读取业务键，保留已有原文及来源，不读设置、不调用模型。

## 真实记录与审阅意见

用户已验W1首版/两轮修订及W2 Amazon；三份下载已独立读取核对并原样归档docs/verification/live-20261031。W1 V1–V3保留两条原始反馈，同一correlation_id；W2仅Amazon，provider均real、model均deepseek-flash。TXT与JSON仅导出时间不同，业务内容一致，SHA清单齐全。

这不是原始HTTP日志，没有每版生成时间/请求ID/用量，不补造。W1 requested_quantity仍null，500来自询盘文字。W2“Compact design”属于无尺寸依据的推断，正式展示前应人工删除或改成已有依据的便携用途；原模型输出保留不改，以便审查局限。

## 已知限制

- 知识全是自建模拟，不是官方平台/国家规则或供应商真实性验证；缺认证/报价/交期时提示待确认，不完成法律判断。
- 真实生成仍可能额外推断，有限三词筛查不覆盖所有语义；没有全量事实校验，不保证绝不幻觉。
- 默认模拟；真实模式需本机设置和用户点击。Key仅当前会话，无.env读取；HTTPS不跳转、不关闭TLS验证、无自动重试，不保存错误正文。
- 同步请求，60秒网络等待不保证端到端耗时；1MiB请求/返回，W1/W2分别1800/4096输出tokens。
- 历史仅会话；支持JSON/TXT手动导出，未支持自动持久化、任意导入恢复、W2多轮、流式事件或外部知识更新。

阶段提交不代发邮件、不合并、不创建最终tag。可由团队审阅后另行决定集成分支、正式知识来源及人工修订后的演示材料。
