# B对接A V3：调用、验证及边界

来源：根项目用户提供B_V3接入说明.md；实际从GitHub feature/foundation-contracts核实并导入2ce533b。其docs/B_V3接入说明.md中的“待推送”是历史描述，本轮已核实该提交发布。所有接入改动在B分支，A/main分支未修改。

## 改动与公共契约

同步A的core/identity、core/v3、schemas.context、AuthService、CaseAccessService、GenerationService、AttachmentService、SearchService、KnowledgeService及storage schema2，不复制模型，不直接写SQLite业务表。README/app冲突逐项处理：A登录/Case面板＋B服务编排，旧main不再作为产品调用入口。

B新增workflows/v3_service.py、v3_ui.py和knowledge/published.py、manage_ui.py；纯provider业务函数新增可选reference_context/attachment_context，以复用原生成与规则，不新建第二套业务实现。

既有旧面板UI测试转用tests/fixtures/b_panels_app.py；它不是产品路由或测试模式旁路。A测试中唯一依赖退休main流式实现的前端用例已改为新B界面，仍检查SQLite保存、两轮请求、刷新后读取及工作流隔离；A其余服务实现及测试断言保留。

## 调用样例

```python
from auth import AuthService
from core.schemas import SalesRequest
from core.v3 import RequestContext
from storage.bootstrap import open_store
from workflows.v3_service import BWorkflowService
from providers.mock_client import MockChatClient

auth = AuthService(open_store())
# token由本机登录取得；不得写入源码、请求、日志、模型或导出。
request = SalesRequest("Need 500 lamps", "USB, ABS, 3 modes", "English", "Germany", 500,
                       context=RequestContext(case_id))
business = BWorkflowService(auth)
first = business.sales(token, request, MockChatClient())
second = business.sales(token, request, MockChatClient(), feedback="Please confirm packaging", parent=first.artifact_id)
backup = business.export_case(token, case_id)
```

真实回调改为本机会话配置的HttpChatClient；调用顺序为authorize_request → 读取适用发布版本/C ready附件 → B生成/规则 → save_result（重核身份和上下文）→ 受权会话索引。未经许可、未分配Case、伪造actor、关闭Case及变更中的上下文不会进入正常模型流程。AI失败不保存正常稿。

GenerationArtifact是成功原稿的权威记录；会话索引是第二次独立乐观锁写入，并非跨两者的原子事务。如索引写入失败，错误说明原稿已经保存并提供artifact_id，需从Case历史找回，不直接收费重试。未声称两次写入整体原子。

## 五项服务对接

1. 登录/Case：复用A面板与受权服务，当前角色生效。切Case/用户/角色清除B临时表单；新用户清除旧模型Key/确认。被撤销分配后不显示旧Case数据。
2. 生成与人工稿：W1/W2/候选/模拟均保存为A GenerationArtifact。候选为sales工作流中`{"inquiry_candidates":...}`的具名JSON结果，UI用artifact_kind明确分类，不当客户正文或最终稿。模拟使用simulate=True/origin=simulation。人工稿走edit，父稿保留，独立复核；每次显示/复核执行当前规则重检。
3. 附件：通过AttachmentService读取，选择器仅ready，服务端再次检查Case/版本/状态。模型载荷保留来源定位，raw/candidate与confirmed分开，只接受A重建的人工确认字段。当前没有B上传按钮；C负责原件与解析器，不构造假解析结果。
4. 搜索：`search_customer(..., adapter=server_owned_callback)`使用A的SearchService。未配置时受权失败记录可见；demo=True另存明确DEMO、URL为空，不用假网页兜底。真实搜索供应商/Key尚未配置，回调测试不是联网证据。
5. 知识：正式检索仅list_published/get_published，固定历史产品可get_version读取已发布/归档版本；按日期/国家/平台/明确产品范围过滤。不以相似产品名套参数，不把8条JSON自动发布。可草稿、提交、独立审核、发布、新版本、回滚，独立审查与demo禁止发布由服务端执行。

版本快照SHA只覆盖不可变内容/来源/适用条件/创建版本字段，不包含后续归档/回滚改变的生命周期元数据，避免同一固定版本归档后摘要无故变化。

## 测试与新验收（一次完成）

全套`python -B -m unittest discover -s tests -v`：145项通过（原B101+A新增32+B接入12）。最终历史版本选择与稳定摘要又跑12项接入测试。没有新依赖，语法按UTF-8 BOM兼容检查。真实模型/真实搜索未调用；本机生产账号未创建，所有测试账号/数据仅临时库，数据库不分发。

用户可在本机一次验证：

- 打开app.py，创建初始管理员（至少12位密码）或登录已有账号；导入演示Case。
- 创建客服/运营和独立复核账号，分配Case；检查无授权角色灰显、无分配时不能生成。
- 模拟W1首版/两轮、W2所选平台、候选和模拟客户，刷新/重新登录后从Case历史读回，检查不同Case不串数据。
- 产品/知识demo草稿可保存、独立审核，但管理员也不能将其直接发布为正式事实。正式来源只用真实有依据资料验证发布/回滚，不把模拟数据改标local绕过。
- 另存人工稿、由另一账号复核；原稿保留，模拟客户不能审批为业务稿。
- C尚无ready附件时显示暂无解析结果；搜索未配置时提示不可用，独立演示查询明确DEMO。
- 下载当前Case JSON/TXT，检查原/人工/模拟来源与有权限的反馈索引；不含会话token、密码或API设置。

旧1—14项验证的是旧B功能，不自动等于新账号/Case接入验收。无需重做旧真实付费生成才能证明本次离线接入，真实接口配置可在新权限流程下再由用户主动验证。

## 仍需A/C/D及服务配置

- C原件上传/OCR/PDF/XLSX/CSV解析器与下载路由未合入；B读接口已接，不伪称附件全链已完成。
- 搜索实际provider尚无服务配置，不能演示真实背调资质。服务只保存实际adapter返回的URL，不验证网站真实性。
- 已发布任意regex规则暂不在UI线程执行，统一提示需D有界执行器/人工；exact和synonym支持。没有完整法规/语义判断。
- A的edit没有新risks/pending写回参数：保存旧风险、B实时展示/审批前重检，不伪造持久风险已更新。建议A后续提供明确重检保存接口。
- A Artifact未原生保存AI修订feedback/parent；B使用WorkflowConversation具名索引。当前get_conversation要求对应生成权限，复核员可读原稿但未获反馈索引权限；导出只带当前岗位能读取的索引。建议A提供只读Case反馈接口及原生修订关联。
- 新会话须重新登录、重新填写模型Key；数据持久化不等于浏览器自动登录，也不自动把旧会话TXT/JSON导入或批准。

本轮不修改共享core契约，不替队长合并，不发布最终tag，不自动给任何客户或队友发送消息。
