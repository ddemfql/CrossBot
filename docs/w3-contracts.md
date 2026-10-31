# W3 接口与实现边界（第一版）

## 当前简化工作台契约

按最新产品要求，界面仅有管理员和业务员，业务员拥有全部交易操作权限；管理员另外开通账号。旧岗位账号在身份存储边界映射为业务员，不重写历史审计。原有严格批准、运输和收款服务供历史数据兼容，主界面不再要求办理这些流程。

`documents.simple_workbench` 展示本单所需或已存在的文件类别、缺失提示、文件版本和所选文件导出。`CaseService.set_required_documents` 保存本单基础清单，继续合并适用的 RequirementPolicy。草稿允许缺失字段，不自动填造业务事实。

`SimpleExportService.generate(case_id, actor, document_type, human_confirmed=True, output_format="xlsx")` 使用既有 WorkspaceRepository/BlobStore/GeneratedArtifact 契约，支持 XLSX 与标准排版 PDF。当前文件必需数据齐备、适用确定性规则通过及明确真人确认共同决定正式生成；未完成检查仍可下载待核对预览。确认绑定产物、输入摘要、来源、规则及模板。生成不会将 Case 改成 PASSED，不生成外部主体签发的文书。修改资料使旧产物失效，重新生成追加独立版本。`TemplateService` 支持受限 XLSX 占位符模板上传、版本化和选择；不要求必须上传模板。

### 资料需求与文件选项

- 公共 `DataRequirement` 表达目标文件、字段/明细行、当前值、是否必需、来源和待补状态。`documents.requirements.data_requirements(workspace, kind)` 为库存、审核和导出共用的只读入口。
- `Workspace.required_source_types`、`required_delivery_types` 保存用户清单；`None` 兼容旧快照按类型分组，空列表表示明确未选择。`CaseService.set_document_checklist(..., expected_revision=None)` 保存两类清单并保留适用条件规则。
- `ManualDataRecord` 提供独立记录 ID、类型、名称、值、明细、修订及录入审计；同类多份 PO 不合并。`FormSourceReference.record_id` 绑定指定记录修订，空值兼容旧表单来源。
- `DocumentForm.included_fields`、`included_row_fields`、`included_context_fields` 分别选择表头、明细列与补充资料，`None` 保留目录默认。`FormService.configure_fields(..., expected_revision=None, context_fields=None)` 调整可选项，保留核心必需字段和适用条件要求；关闭项保存原值供再次启用，但不进入该输出。保存和取值通过 `form_fields`、`form_row_fields`、`form_context_fields` 统一。
- `RequirementInputService.upload(case_id, actor, target_type, field_name, row_number, filename, content, source_type=None, expected_revision=None)` 保存真实原件和 `SourceRequirementLink`，精确绑定资料项与来源版本。`collect_document(..., prefill=False)` 允许只采集；`PrefillService.fill_requirement(...)` 仅尝试填写该项，不能唯一识别则保持待补，不改写无关内容或人工清空。
- `ReviewedSourceRow` 和 `SourceRowSelection` 用于完整原件商品行的明确人工核对，保留原值、修正值、位置、来源版本及核对人。核对不可删除原件行、重复身份或忽略额外商品。
- `DocumentTemplate` 保留模板类型、版本、哈希、文件及上传记录；`Workspace.selected_template_ids` 绑定每类文件当前模板。当前支持表头占位符和单条明细重复行，不接受公式、宏、外链或图表。PDF 不仿照自定义 Excel 排版。

新增契约均使用既有追加快照与 BlobStore，不新增私有持久化接口；`TradeData/TradeLine.supplied_fields` 区分明确提供字段与创建时的初始值。规则升级、选项修改、来源改版和模板选择变化使旧确认失效。历史下载保留，当前有效交付依据现行规则和输入摘要判断。

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


## 精简工作台与个人默认（2026-10-31）

`UserWorkbenchDefaults`/`UserDocumentDefaults` 保存认证用户的文件清单、输出字段选择及独立的必需输入选择；不包含交易业务值。`core.ports.UserDefaultsStore` 由 `storage.preferences.SQLiteUserDefaultsStore` 实现，按用户追加不可变版本并比较期望偏好版本。`UserDefaultsService` 仅操作本人配置，新建 Case 记录默认用户、版本和配置快照，旧单及共享单不会因切换账号自动变化；明确应用默认保留资料并使受影响产物重新核验。

`DocumentForm.required_input_fields/required_input_row_fields/required_input_context_fields` 独立于 included_* 输出选择。`input_*` 帮助函数提供录入、完整性及审核输入，`effective_*` 仅提供输出内容。必需但不打印的资料仍可人工维护且保留来源；强制业务条件不被个人选项绕过。输入配置及默认快照参与审核摘要。

`SimpleExportService.preview/generate` 支持 audience=customer/internal；服务兼容默认 internal，工作台默认 customer。客户版保留选定业务内容，内部来源、规则和审计由内部版及系统保存；用途写入产物溯源。模板上传需 template_manage 权限，仅管理员具备，业务员可选择已有版式并独立执行全部业务核对和生成。


## 审核问题直接修改（2026-10-31）

`CrossCheckResult.edit_targets` 使用公共 `ReviewEditTarget` / `ReviewEditArea` 定位实际输入所有者，包含文档类型、字段、行号及可选原件 ID/版本/表索引、人工资料记录 ID。默认空列表兼容历史快照；定位由规则输入构造，不解析中文问题或来源显示字符串。当前规则版本为 `w3-export-review-7`。只有人工确认尚未完成的一致项不会伪装成内容冲突。

`ReviewEditingService.save` 在保存前校验认证权限、当前工作区修订、审核摘要及当前问题允许的精确目标，复用 FormService、CaseService 和 ManualInputService 修订指定输入。表单隐藏值、其他字段、继承明细和人工记录身份保留；局部人工核实只归属所选字段。原件修改通过既有 confirm_source_fields/confirm_source_rows 保存独立核对值，原始内容与机器候选保持不变；每份原件商品行仍需完整核对。修改后重新评估并失效旧确认及产物，不批准 Case 或自动生成。

界面将日常选择交易、缺项、交付状态及两个操作页签前置，审核区可直接选择问题和修改位置。清单设置统一使用“需生成的内容”下方折叠的“调整资料与交付清单”入口。完整编辑、版式、来源、历史与打包默认折叠；新建/导入和账号管理在已有交易时后移，无交易时仍直接提供创建入口。快捷下载只提供当前选中文件类型、格式、用途的有效产物；历史下载需明确选择。


## 缺项直接录入

`RequirementInputService.manual_target` 从当前 canonical data_requirements 和有效录入字段解析 ReviewEditTarget；支持必需输入及已启用可选输入，拒绝未启用字段、无效行与外部签发目标。`fill` 校验认证权限、工作区修订及审核输入摘要，复用 ReviewEditingService 的修改引擎，只修改所选表头/明细格/整表或订单补充资料。它不依赖该值已被列成审核错误，因此可先填尚空的可选项；审核修改入口原有“当前不通过规则目标”限制继续保留。两种入口共享人工来源、修订及产物失效行为，不自动批准或生成，不新增原件。


## 审核与工作台统一收尾（2026-10-31）

审核规则版本更新为 `w3-export-review-8`：除必需项缺失外，已选用且非空的可选输入若来源待核实也阻止正式生成；双方均空且并非适用必需项的可选比对跳过，任一方有值或明确要求的项目继续核对。保持精确商品身份及明确单位，不进行模糊配对。

单项表单修改的保存审计记录精确修改范围及未变单元格的原来源，避免继承明细物化后给其他默认/未知值补造人工依据。订单中的 USD/pcs/零值没有明确 supplied_fields 标记时继续作为未核实默认值；有依据的真实零值使用明确标记。预填仅读取当前原件版本、表索引、行号及机器原值完全匹配且唯一的 ReviewedSourceRow，候选和原表保留，修正值注明实际核对人及时间。撤销、改版或原值变动不会沿用旧核对。

`documents.drafts.DraftStore` 只管理当前会话内的填写暂存，无数据库自动保存。范围包含账号、交易、编辑器及明确目标，记录原始版本、原值及当前业务摘要。默认使用严格修订检查；只有服务端计算的输入摘要与原始摘要相同、目标原值也相同时，才能忽略下载审计等版本噪声。业务变化后保留暂存但禁止旧值覆盖；明确放弃旋转控件键，退出登录清理全部暂存。页面可查看与放弃已关闭字段的暂存，避免无法解除生成阻挡。

当前交付状态与快捷下载复用 current_artifacts；历史/旧规则产物明确提示重新核对。当前缺项图、生成选择和交付清单只展示已选目标，历史产物不隐式恢复已移除目标；历史下载独立保留。付款通知等无明细文件仅在确实启用明细时要求商品身份。编号/日期日常编辑统一展示一组，历史别名差异仍由审核控制。
