# 给 B：V3 公共契约与服务接入说明

版本：3.0.0，日期：2026-10-31。对应 `core.v3.CONTRACT_VERSION`。

本文回应 `b-v3-contract-requests.md` 中的五项 A 依赖。代码目前在 A 的
`feature/foundation-contracts` 工作区，待队长手动提交、推送；不要把本文当作远程已发布通知。
收到队长提供的新提交号后，再同步该基线。不要使用本地数据库文件作为分发物。

## 1. 已交付的五组能力

| B 请求 | A 的交付 |
|---|---|
| 身份、角色、Case 分配、核权 | `core/identity.py`、`auth/service.py`、`auth/cases.py` |
| Case 关联请求与输出版本 | `core/v3.py` 的 RequestContext / GenerationArtifact，`workflows/access.py` |
| 附件读取与字段来源 | `documents/access.py`、AttachmentRef / AttachmentContent / FieldRevision / EvidenceLocation |
| 外部搜索引用与授权 | `providers/search_contract.py`、SearchCitation / SearchReport / SourceKind |
| 产品、知识、词项版本治理 | ResourceVersion / Publication / 三种内容模型，`knowledge/governance.py` |

`storage/v3_store.py` 提供事务和审计；页面和供应商适配器不要直接写这些表。
业务服务是进程内 Python API，不是已上线的 HTTP 路由。

## 2. 本地启动与账号

```powershell
cd D:\hackathon\CrossBot
.\.venv\Scripts\python.exe -m storage --demo
.\.venv\Scripts\python.exe -m streamlit run app.py
```

第一次打开网页创建管理员，密码至少 12 位。无内置通用密码。
也可以运行 `python -m auth admin`，按隐藏输入提示创建第一个管理员。
初始化入口只在数据库尚无账号时可用；已有管理员时不能再次执行初始化来取得权限。

管理员在侧栏创建成员账号并分配角色、Case。角色可多选；只有当前激活角色的权限生效，
不是将账号所有角色的权限取并集。管理员默认只有 admin 角色，但该角色拥有管理权限。
没有授权的角色按钮灰显。当前界面的生成页仍兼容调用旧 main.py；B 业务合并时用本说明的服务替换。

数据库默认 `data/crossbot.db`，首次打开自动升级到 schema 2，保留之前的 Case 和对话。
用户、会话、Case 分配、版本、发布历史、附件识别、搜索和生成稿均持久化。
密码为带随机盐的 PBKDF2 哈希，数据库只保存会话令牌的 SHA-256 摘要。
会话有效期 8 小时；账号连续 5 次失败会暂时锁定 5 分钟。
登录、退出、角色变更、Case 分配、发布和回滚有审计记录。

## 3. 登录主体和权限：必须在模型/敏感读取之前检查

```python
from auth import AuthService
from auth.cases import CaseAccessService
from core.identity import Permission, Role
from storage.bootstrap import open_store

store = open_store()
auth = AuthService(store)
token = auth.login(username, password)
principal = auth.require_session(token)
auth.require_permission(token, Permission.W1_GENERATE, case_id)

# 必须是账号已获授权的角色；服务器保存当前角色。
auth.switch_role(token, Role.CUSTOMER_SERVICE)
cases = CaseAccessService(auth).list_cases(token)
```

token 只存服务端会话，不放入模型请求、搜索词、日志或业务 JSON。
`Principal` 是展示用快照；服务接口接受 token 并重新查账号/角色/有效期，
不接受调用方自己构造的 Principal 或 role 作为凭证。
账号停用/权限修改会撤销既有会话；撤销 Case 分配即时生效。

统一异常为 `CrossBotError`，错误码来自 `core.errors.ErrorCode`：
`unauthenticated`、`permission_denied`、`account_disabled`、`not_found`、`conflict`、
`review_required` 等。通过 `error.to_dict()` 转为 UI 错误；不要混入模型正文。

### 权限决策（A 已统一）

| 当前角色 | 允许的主要操作 |
|---|---|
| admin | 账号管理、分配 Case、审批、发布/回滚及全部业务权限 |
| customer_service | 自己被分配的 Case、W1、客户模拟、客户附件上传、外部搜索 |
| product | 已分配 Case、产品/知识/违禁词草稿、产品附件上传 |
| operations | 已分配 Case、W2；不能发布知识 |
| document | 已分配 Case、单证写入/确认、一般状态流转、交叉比对 |
| reviewer | 已分配 Case、字段确认、生成稿复核、知识审批、Case 放行/关闭 |

所有角色可读取已发布知识；Case 数据仍受分配范围限制。管理员可跨 Case。
客服不能改产品主数据或关闭 Case。复核员可审批知识，但不能发布/回滚。
任何提交人（包括管理员）都不能自审。管理员可发布由其他人独立审批过的自身草稿。

账号管理服务还提供 `create_user(token, username, password, display_name, roles)`、
`update_user(token, user_id, roles=..., status=...)`、`list_users(token)`、
`assign_case(token, case_id, user_id)` 和 `revoke_case(token, case_id, user_id)`。
这些操作均要求管理员权限。更新账号权限后需要重新登录。

## 4. Case 与业务请求上下文

原 `SalesRequest` / `ListingRequest` 保留原参数顺序，新增可选 `context`，
纯数据或旧测试仍可构造不带 context 的对象；调用 V3 服务必须带 context。

```python
from core.schemas import SalesRequest, ListingRequest
from core.v3 import RequestContext, AttachmentRef

request = SalesRequest(
    inquiry="Need 500 USB lamps. Please confirm delivery time.",
    product_info="USB powered, ABS, three color settings; delivery unconfirmed",
    target_language="English",
    customer_country="Germany",
    context=RequestContext(
        case_id=case_id,
        product_version_id=published_product_version_id,  # 允许为空
        attachments=[AttachmentRef(document_id, document_version_id)],
    ),
)
```

- actor_id 由服务端填入；填其他人的 ID 会被拒绝。
- correlation_id 仍是请求追踪号，绝不能充当 Case ID。
- 产品版本必须已正式发布过；历史归档的已发布版本可作为 Case 固定版本引用。
- 不带产品版本的手工 product_info 只是用户补充资料，不因此成为已审核主数据。
- 附件 Case 必须匹配，且解析状态 ready 才可作为模型输入。
- 调用方自填的 confirmed_fields 会被丢弃，只从保存的人工确认结果重建。
- 知识和条目为 demo/external_reference 时，不能直接发布为正式事实。

## 5. 生成、流式结果、人工编辑的保存

### 非流式

```python
from workflows.access import GenerationService
from core.schemas import GenerationResponse

service = GenerationService(auth)

def model_provider(authorized_request):
    # B 在这里装配提示词并调用真实 API。
    # API key/token 都不要写入 request 或 context。
    return GenerationResponse(
        content=generated_text,
        risks=risk_items,  # list[core.models.RiskItem]
        pending_confirmations=["Delivery date"],
        provider="configured-provider",
        model=configured_model_name,
    )

artifact = service.generate(token, request, model_provider)
```

服务先核权、解析引用，再调用 provider；未登录/未分配/角色错误时不会调用 provider。
模型回调异常不会伪造成功记录，B 将异常映射为统一错误即可。
输出存为独立 GenerationArtifact，记录输入快照、Case、主体、产品/附件版本、模型、风险和待确认项。
历史列表可用 `service.list_for_case(token, case_id, workflow="sales")`，单条使用 read。

### 流式（B 可沿用自己的流式适配）

```python
authorized = service.authorize_request(token, request)  # 必须在发起 API 前
# 在这里流式调用 provider，累积完整文本并执行合规检查。
response = GenerationResponse(content=full_text, risks=risk_items)
artifact = service.save_result(token, authorized, response)
```

保存时再次核权；授权上下文变更返回 conflict，不能把旧结果伪装为新上下文结果。
流式路径需要 B 自己检查错误事件，只有完整成功结果才调用 save_result。

```python
manual = service.edit(token, artifact.artifact_id, edited_text)
approved = service.review(reviewer_token, manual.artifact_id,
                          approve=True, reason="Checked source fields")
```

人工稿另建记录并引用 parent_artifact_id，AI 原稿保留。复核人不能是该稿作者。
修改后的风险与待确认信息不会自动清除；B 每轮必须重新运行合规规则。
客户模拟调用 `service.generate(token, request, provider, simulate=True)`，
结果 origin=simulation，不能直接变为业务最终稿或当作真实客户消息。
这些方法不向客户发消息，不接入邮件/阿里等发送渠道。

## 6. C 附件交接：B 只读解析结果

```python
from documents.access import AttachmentService
from core.v3 import AttachmentRef

attachments = AttachmentService(auth)
parsed = attachments.read(token, AttachmentRef(document_id, version_id), require_ready=True)
# parsed.text / parsed.fields / parsed.method / parsed.status / parsed.error
```

附件状态：ready、pending_review、failed。解析器未接好、空结果或置信度低于 0.8 时不放行。
这是演示阶段的可调整技术阈值，不代表真实识别准确率。

字段采用 FieldRevision，保留 raw_value、candidate_value、confirmed_value、confirmed_by、
confirmed_at 和 history。EvidenceLocation 支持 PDF 页码、图片矩形、文本摘录、
Excel 工作表/单元格、CSV 行号/列名。列表字段用 items[0].quantity 等唯一字段路径。

### 给 C 的约定

1. C 保存原件字节，不覆盖已有文件；计算真实 SHA-256。
2. 创建 Document / DocumentVersion（版本 document_id 必须与文档一致）。
3. 调用 `attachments.save_document(token, document)` 保存原件元数据。
4. 调用 `attachments.register(token, content)` 写入一次解析结果。
5. 同一个版本不可覆盖结果；重新解析建立新版本。

```python
from core.v3 import AttachmentContent, AttachmentStatus, FieldRevision, EvidenceLocation

content = AttachmentContent(
    case_id, AttachmentRef(document_id, version_id), method="xlsx",
    status=AttachmentStatus.READY, confidence=0.98, text="Quantity: 500",
    fields=[FieldRevision("quantity", raw_value="500", candidate_value=500,
                          location=EvidenceLocation(sheet_name="Packing", cell_reference="B2"))],
)
attachments.register(document_user_token, content)
attachments.confirm_field(reviewer_token, content.reference, "quantity", 500,
                          reason="Read original B2")
```

解析器不能自填确认人/最终值。低质量结果需要逐项确认后，由有权限岗位调用
`approve_quality(token, reference, reason=...)`；failed 不能强制改 ready，必须新版本重新解析。
确认和质量变更同步投影到现有 DocumentVersion.fields，C/D 可继续消费公共模型。
原始读取元数据接口是 `get_document_version(token, reference)`；它不会提供任意路径读取能力。
实际 OCR/PDF/XLSX 解析器与文件下载路由仍由 C 开发，B 不另写一套。
可用 `attachments.list_for_case(token, case_id)` 查看已有解析结果，包括待处理/失败项。

## 7. 外部搜索保底

```python
from providers.search_contract import SearchService
from core.v3 import SourceKind, SearchCitation

report = SearchService(auth).execute(
    token, case_id=case_id, query=company_name, purpose="Verify supplied company name",
    source_kind=SourceKind.EXTERNAL_REFERENCE,
    provider=actual_search_adapter,
)
```

adapter 由 B 在服务器配置，签名为 `query -> list[SearchCitation]`。
搜索前保存授权审计，返回结果再关联该记录和 Case；调用者不能自报 actor。
引用字段为 title、url、retrieved_at（带时区）、matched_name、confidence、excerpt、review_status。
所有新结果强制 pending_review，不自动认定公司资质或写入主档。

来源类型 local / authorized_api / external_reference / demo 必须真实区分。
服务仅验证引用结构和保存来源，无法替代供应商核验页面真实性；B 只能把实际返回的网页交给该服务。
模型生成 URL、固定模板、猜测内容均不得标为 external_reference。
外部网络失败返回 provider_unavailable，并保留失败审计；
若要回退固定样例，另调用 source_kind=DEMO，页面明确显示演示数据。
真实搜索服务、Key 和联网配置尚未配置，A 未替 B 实现真实网页搜索。
历史记录使用 `SearchService.read(token, search_id)` 或 `list_for_case(token, case_id)`。

## 8. 产品、知识与违禁词版本

```python
from knowledge.governance import KnowledgeService
from core.models import to_dict
from core.v3 import ProductData, ResourceKind, SourceKind

knowledge = KnowledgeService(auth)
draft = knowledge.create_draft(
    product_token,
    resource_id="product-usb-lamp",
    kind=ResourceKind.PRODUCT,
    content=to_dict(ProductData(name="USB desk lamp", sku="LAMP-001",
                               specifications={"color_settings": 3}, materials=["ABS"])),
    source="Manufacturer specification reviewed by product team",
    source_kind=SourceKind.LOCAL,
    effective_date="2026-10-31", market="Germany",
)
knowledge.submit(product_token, draft.version_id)
knowledge.review(reviewer_token, draft.version_id, approve=True, reason="Checked original specification")
knowledge.publish(admin_token, draft.version_id)
published = knowledge.get_published(customer_service_token, "product-usb-lamp")
```

知识正文可使用 KnowledgeData，违禁词正文可使用 ProhibitedTermData。
语言、市场、平台、来源、生效日期和 product_scope 放在 ResourceVersion 顶层。
pattern_type 支持 exact / regex / synonym；regex 在保存时做语法检查，运行效率和匹配规则由 B/D 验证。

状态：draft → in_review → approved/rejected → published → archived。
内容创建后不原地修改；编辑通过 `create_draft(..., parent_version_id=old_version_id)` 创建新版本。
发布新版本会归档上次活动版本。正式检索必须调用 list_published / get_published，
不能直接枚举所有 JSON/数据库行，也不能把草稿或演示 8 条数据当作正式规则。

```python
knowledge.rollback(admin_token, resource_id, old_published_version_id,
                   reason="Restore verified wording")
history = knowledge.publications(token, resource_id)
```

回滚追加 Publication，不删除版本或历史；原文不覆盖。发布、归档、回滚及审计在同一事务提交，失败全部回滚。
ResourceVersion 记录 created_by、reviewed_by、published_at、archived_at 和父版本。
草稿/审批队列使用 `knowledge.list_versions(token, status=LifecycleStatus.IN_REVIEW)`。
普通账号仅看到自己的未发布版本和已发布历史；管理员、复核员可查看审核队列。
该列表不是正式检索入口，正式检索只用 list_published / get_published。

## 9. B 的迁移顺序与不做事项

1. 同步队长提供的 A 提交；保留你分支自己的业务代码，手工处理入口冲突。
2. 用 AuthService 和 CaseAccessService 替换页面自选角色与无授权读取。
3. W1/W2 构造带 RequestContext 的请求，API 前 authorize_request。
4. 对接 C 的 AttachmentContent，不能用 raw/candidate 冒充 confirmed。
5. 用 GenerationService 保存模型原稿和人工稿。
6. 用 SearchService 包装实际搜索/明确的演示回退。
7. 用 KnowledgeService 接入草稿、审核、发布、归档和回滚。

不要更改 `.env`、分发数据库、提交 token 或密码、直接改 JSON 绕过审核、
复制权限模型、使用原始 SQLiteStore 作为面向请求的业务 API。
SQLiteStore 仍保留给内部初始化/迁移/测试；它不是安全边界，不能暴露给不可信调用者。
当前 app.py 是 A 的最小登录/Case 演示入口，合并时以 B 的功能页为主逐项接入，不能整文件盲选覆盖。

## 10. 验证与交接边界

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

重点看 tests/test_v3_services.py 与 tests/test_database_integration.py。
测试覆盖未登录/过期/禁用、角色伪造、未分配 Case、角色切换、独立审核、
演示发布阻断、版本回滚、事务失败、附件失败/低置信度、流式保存冲突和 AI/人工稿分离。

A 本地本轮结果：42 项测试通过；36 个 Python 文件通过语法解析。
现有本地库升级至 schema 2 前已备份，升级后完整性检查正常，原演示 Case 保留。
本地库尚未创建账号；首次打开网页创建初始管理员，不存在公开通用演示密码。

A 提供基础服务和测试，不代表全队 V3 业务已完成：真实模型质量、外部搜索、
阿里背调、OCR/PDF/Excel 解析、B 的知识管理页和 C/D 的完整 W3 仍需各分支联调。
数据库 schema=2 与契约版本=3.0.0 是两个独立版本号。

收到队长“已推送 + 新提交号”之后再开始对接。完成后交回调用样例、测试结果和仍需调整的契约项。
