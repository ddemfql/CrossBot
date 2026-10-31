# W3 接口与实现边界（第一版）

## 当前简化工作台契约

按最新产品要求，界面仅有管理员和业务员，业务员拥有全部交易操作权限；管理员另外开通账号。旧岗位账号在身份存储边界映射为业务员，不重写历史审计。原有严格批准、运输和收款服务供历史数据兼容，主界面不再要求办理这些流程。

`documents.simple_workbench` 展示本单所需或已存在的文件类别、缺失提示、文件版本和所选文件导出。`CaseService.set_required_documents` 保存本单基础清单，继续合并适用的 RequirementPolicy。草稿允许缺失字段，不自动填造业务事实。

`SimpleExportService.generate(case_id, actor, document_type, human_confirmed=True)` 使用既有 WorkspaceRepository/BlobStore/GeneratedArtifact 契约，生成无需用户模板的简单 XLSX。明确的真人确认是生成条件，缺失和冲突作为检查提示保存，不要求旧流程中的表单逐字段确认或批准。确认保存在审计中并绑定产物和输入快照；生成不会将 Case 改成 PASSED，不生成外部主体签发的文书。修改资料使旧产物失效，重新生成追加独立版本。内部布局版本/哈希沿用产物模板溯源字段，不表示用户必须配置模板。当前不支持自定义模板上传或直接生成 PDF。

以下为早期完整流程的模型和兼容服务说明；与上述简化界面不一致时，以上述当前契约为准。

## 采集填表契约补充

`DocumentForm` 兼容追加 `field_candidates`、`row_candidates`、`auto_values`、`auto_rows`、`manual_fields`、`rows_manually_edited`。旧快照缺少这些属性时按默认值读取。`FormFieldCandidate` 和 `FormRowCandidate` 是 `core.models` 公共类型，记录未确认候选、来源文档版本、页码、工作表和单元格/行位置。后续完整性核对、人工核对及校验模块直接读取这些结构，不依赖界面字符串，不把自动填写视为人工批准。

`PrefillService.fill(case_id, actor, kind, document_ids)` 读取本交易指定来源的当前采集结果并填入目标表单；空来源列表撤下机器填写、保留人工维护内容。`collect_document(..., target_type=None, ocr_images=False)` 在同一工作区保存采集与对应预填；ZIP 初始化、重新分类、文件移除和版本替换复用同一映射/刷新逻辑。订单主数据不会被预填服务改写。已确认字段优先作为来源值，但填入另一张表单仍不会自动确认该表单。

`effective_values(trade, form)` 和 `effective_rows(trade, form)` 是表单显示、编辑和导出的统一取值入口。人工明确清空的字段/明细保持为空。未被人工维护的空项才允许使用订单基础数据；不要求每个业务值绑定文件，手工录入由审计记录人员、时间和前后值。重新采集使用当前机器结果，已确认或人工来源字段保留；采集完全相同不会重复生成待办或失效产物。

使用既有 WorkspaceRepository 的 compare-and-save 与追加快照，不新增私有存储接口。改动应同步给公共契约、存储、人工核对和校验模块维护者；无需新增存储表，旧审计和快照不改写。生成仍使用既有显式人工确认入口；完整性规则、人工核对业务和跨文件规则的扩展另行负责。

`FormValueOrigin` 为只读公共展示契约，`documents.provenance.form_value_origins(workspace, kind)` 为每个有效字段/明细单元格解释实际来源，区分 file/manual/trade/default/unknown/derived；候选与实际来源分开。人工来源依赖可核实的填写审计，旧保护标记本身不足以证明人工录入；重复 SKU、删行或变更后无法确定原单元格时保守标为来源未核实。

`SimpleExportService.preview(case_id, actor, document_type) -> RenderedOutput` 仅检查读取权限并生成待人工核对预览字节，不写数据库、blob、审计、正式版本或批准。预览明确标注未确认且确认人留空；与正式 generate 共用取值、来源记录、排版及只读规则提示。自动测试可使用该接口检查导出，不模拟真人确认。

## cross-check-quality 合并与严格生成门槛

已合并来源分支提交 `de40318`。`validation.rules` 保留来源分支的纯比较函数，并重导出 `validation.workspace_rules` 中既有 W3 适配器；`normalizers` 和 `cross_checker` 原有样例/测试保留。公共 `from_dict` 统一在 `core.models`，`core.serialization` 仅兼容导入，Case 金额按 Decimal 无损恢复。共享 Case 的所需文件与 Workspace 清单在交易服务保存时同步，导入空清单不隐式变成固定 CI/PL 要求。

`ExportReviewReport` 是当前文件的只读报告，包含规则版本、精确输入摘要、human_confirmed 及 checks；只有非空必需规则全部 PASS 且明确真人确认才 ready。`CrossCheckResult` 兼容新增规范化两侧值、单位、比较策略、scope、item_key、required 字段。整单资料缺失使用 required=False 提示，不代表 Case 已完整；不允许仅传入一条 PASS 代替所选文件全部生成规则。

`validation.export_review.review_export` 绑定当前订单、所有保存表单、来源当前版本/字段/表格哈希、需求策略和可核实的人工填写证据。人工确认覆盖当前保存的有效表单，绝不批量把原始机器字段改成已确认。外部原件比对只读取精确、唯一、实际确认的字段及明确商品范围；不能确定明细配对时 PENDING_REVIEW。使用版本化零容差策略、严格十进制格式、显式币种/单位、文字 trim-only 和唯一产品编码，不用模糊匹配消除差异。

`SimpleExportService.assess` 增加对实际关联原件 blob/哈希的只读完整性核验；`generate(..., expected_review_digest=None)` 每次重新评估，摘要过期、必需 FAIL/PENDING、未确认或非真人身份均在输出前拒绝。正式产物的 checks、规则版本、审计中的输入摘要/来源版本/输出哈希共同绑定该次确认。旧产物保留且标示旧规则；严格审核回执可以证明同一表单快照被核对，不伪造 form.confirmed_by 或 Case 通过状态。只读预览保留全部未通过原因，不代表正式文件。

`SourceFieldSelection` 精确指定当前原件版本中的字段与人选定的值。`review.quality_inputs.confirm_source_fields` 要求显式真人动作及匹配输入摘要，原子保存所列字段的 confirmed_value/确认人/时间、审计和复核记录；机器 value 不覆盖。核对后失效旧结果并重新比对，改错的确认值也可以在同一简洁表格更正。来源字段被人工改值后继承到其他表单，来源说明记录修正人/时间及原文件位置，不声称新值原本位于原单元格。

本次 W3 实施先补齐契约再实现适配器。现有公共状态、事件、错误保持兼容；新增交易、产物、身份、审批和收款数据统一位于 `core.models`，不在 W3 内复制领域模型。跨分支集成时，公共契约应先同步至 `feature/foundation-contracts`，再由其他功能分支同步。本地实现不移动分支或覆盖其他成员的改动。

## 公共模型

- `TradeData`/`TradeLine`：订单、买卖双方、产品、目的地、运输/付款条件、十进制字符串金额与数量、确认人和数据修订号。
- `Workspace`：一笔交易聚合；包含 Case、原件版本、复核、检查、生成产物、审批、岗位待办、收款及追加审计。
- `TradeMilestone` 和 `ReceivableStatus`：独立于 `CaseStatus`。`passed` 只表达单证复核通过。发货事实单独记录，不篡改已经批准的交易输入快照。
- `Actor`：应用认证边界传入的具名身份；服务检查认证状态、真人类型和岗位。UI 使用本地密码账户，不能用页面角色选择器伪装批准人。该契约不是外部调用者的身份证明；接入 API 时必须由服务端认证层构造。
- `GeneratedArtifact`：输入快照、来源文件版本、模板版本/哈希、输出哈希和规则结果。
- `ApprovalRecord`：人类批准人的意见、交易修订、原件版本、产物哈希和规则结果摘要。旧批准始终保留；新文件或数据变化使当前产物和批准失效。
- `ExtractedField.assisted_value`/`assistance_note` 保存虚拟业务员的建议及来源；`AssistanceRecord` 逐条记录原件版本、位置、前后值、依据和虚拟身份。建议不会写入人工确认或覆盖机器值。
- `ExtractedField.value` 保存机器候选；`normalized_value` 为机器规范化值；`confirmed_value`、`confirmed_by`、`confirmed_at` 保存人工最终值，不覆盖机器值。

## 跨模块端口

`core.ports` 定义：

| 端口 | 所属模块 | 约定 |
|---|---|---|
| WorkspaceRepository | storage | get/list/save；save 必须比较 expected_revision，在一个事务内追加不可变快照并递增聚合版本；冲突不覆盖 |
| BlobStore | storage | put(bytes) 返回内容 SHA-256；get(key) 验证路径及哈希，原件和生成结果均不可覆盖 |
| IdentityStore | storage | add_user/authenticate；本地账户密码加盐哈希，仅已认证真人身份进入 UI 会话 |
| RulesEngine | validation | evaluate(Workspace) 返回有字段来源/文件版本的 PASS、FAIL 或 PENDING_REVIEW；空值/未确认不能通过 |
| DocumentRenderer | documents | 从已确认 TradeData 生成卖方草稿；模板不负责业务计算；返回内容及模板溯源信息 |

SQLite 使用追加快照实现首版持久化；交易、人工补录、候选值、复核和审计结构化数据都保存在聚合快照中。原件和产物的二进制内容按 SHA-256 放在本机内容存储，路径只使用哈希。数据库和文件默认保存在用户数据目录，不进入仓库。管理员删除当前工作区资料时记录人员、时间、原因并隐藏下载，保留原始内容和不可变历史快照。保存先写 blob 再提交聚合；事务失败可能留下未引用 blob，不会丢失旧版本。

## 文件及规则范围

首版托管 PDF、XLS、XLSX、DOCX、图片和 TXT/CSV。DocumentVersion 另行保留 original_path、source_text、source_tables 和 extraction_warnings，无法解析的文件仍可托管、分类和下载。解析器可读取受支持标签/值布局的文本 PDF、XLS/XLSX、DOCX；图片和无文本层 PDF 用本地 CPU RapidOCR 生成候选。XLSX/DOCX 匹配 allowlist 标签及相邻值，旧 XLS 仅读固定键值行。未识别或冲突时失败关闭；不对任意表格、重复行项目或合规声明正文推断业务事实。新增 pypdf/PDFium 用于 PDF 读取和页面渲染，openpyxl/xlrd 用于 Excel，python-docx 用于 Word，RapidOCR+ONNX 用于图片识字；不引入模型裁决。OCR 输出保留页码和置信度并且只作为机器候选，人工确认仍是通过条件。

目录现有32类文件、18类卖方填写模板；PI、商业发票和装箱单输出为 XLSX，其余卖方模板输出为 HTML。空白及当前填写稿 HTML 可供预览/导出，未确认时明确标记为待核对；保存正式生成版本仍要求主数据及相关表单已确认。生成产物绑定 form_revision/form_snapshot，每次表单变动均需重新生成复核。模板不携带样例交易值。不会生成提单、ISF 回执、证书、签名或官方提交确认。金额使用确定性十进制计算；相同输入产生相同输出字节；每次生成仍创建独立版本。

首版文档需求可按目的地、产品、运输、Incoterm、付款条件配置 `RequirementPolicy`。条件空值表示不限制，命中策略的必需文档取并集；业务人员应核实适用性。默认演示 CI/PL 清单不等于所有交易的法定要求。

## 已知集成事项

`core/`、`storage/`、`validation/` 是本次闭环必要的公共变更，已集中设计并分配单一实现负责人，需各模块负责人在后续集成时同步审阅。首版不实现企业 SSO、租户/案件级隔离、法规数据库、任意模板推断、复杂多行项目识别、法定电子签名或实际海关/承运人提交。OCR 不替代金额、重量及产品行项目的人工核对。审批记录表示业务确认，不表示法定电子签名。已使用授权的私有业务资料核对分类、原件哈希、表单抄录、管理员页面及整单导出；真实资料及导出不进入仓库。新订单仍需逐单核对模板适用性和具体字段。


## 订单全流程与导出

documents.lifecycle 按七个阶段展示留存资料，不把文件存在当作实际阶段完成。documents.workbench.export_bundle 导出原件全部版本、完整 Workspace JSON、32类表单JSON/CSV、18类空白HTML及已有填写稿、可读订单档案、生成产物和辅助整理记录。解压档案链接指向原件相对路径。源文件仍以内容哈希存储；可读名称用于浏览/导出，不替代哈希身份。

review.assistance.apply_transcription 为受权限控制的辅助抄录入口，要求原件、位置及依据，对已确认字段和表单禁止覆盖；保留机器原值及虚拟建议，连续重复不增加版本，后续修正允许回到历史值。该入口不代替人工确认。原件复核待办与岗位办理待办分别保存；外部资料按类型进入销售、单证、物流或财务队列，再由管理员/有权限人员指派到已有账户。系统不推断哪个现实人员已实际接收。

ArchiveImporter 对相同ZIP哈希幂等，拒绝越界、符号链接、加密或超限条目，保留同内容不同路径的文件记录。目录中的业务状态只进入ImportContext未核验声明，CaseStatus与TradeMilestone仍独立。CLI导入使用现有管理员账号，并预先备份SQLite。
