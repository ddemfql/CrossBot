# W3 接口与实现边界（第一版）

## 当前简化工作台契约

按最新产品要求，界面仅有管理员和业务员，业务员拥有全部交易操作权限；管理员另外开通账号。旧岗位账号在身份存储边界映射为业务员，不重写历史审计。原有严格批准、运输和收款服务供历史数据兼容，主界面不再要求办理这些流程。

`documents.simple_workbench` 展示本单所需或已存在的文件类别、缺失提示、文件版本和所选文件导出。`CaseService.set_required_documents` 保存本单基础清单，继续合并适用的 RequirementPolicy。草稿允许缺失字段，不自动填造业务事实。

`SimpleExportService.generate(case_id, actor, document_type, human_confirmed=True)` 使用既有 WorkspaceRepository/BlobStore/GeneratedArtifact 契约，生成无需用户模板的简单 XLSX。明确的真人确认是生成条件，缺失和冲突作为检查提示保存，不要求旧流程中的表单逐字段确认或批准。确认保存在审计中并绑定产物和输入快照；生成不会将 Case 改成 PASSED，不生成外部主体签发的文书。修改资料使旧产物失效，重新生成追加独立版本。内部布局版本/哈希沿用产物模板溯源字段，不表示用户必须配置模板。当前不支持自定义模板上传或直接生成 PDF。

以下为早期完整流程的模型和兼容服务说明；与上述简化界面不一致时，以上述当前契约为准。

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
