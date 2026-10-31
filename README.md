# CrossBot · V3 Case外贸工作台

洪彬斌负责的B分支feature/w1-w2-platform已同步A公共基线2ce533b（契约3.0.0、数据库schema 2），在A登录/Case服务上接入B已有W1/W2、候选、客户模拟、知识和历史功能。未将B合入main/A。

## 当前入口与能力

- app.py先登录、按服务端当前角色核权，再选获分配的Case。模型前authorize_request，保存前save_result重新核权/核对上下文；没有未登录模型入口，也不导入旧main.py。
- W1结构化字段、首版/两轮、失败保留；W2严格按所选平台整批保存。候选用原文精确定位、保留CANDIDATE；客户模拟origin=simulation，不能直接当业务最终稿。
- 生成稿、原请求与产品/附件版本引用持久保存到GenerationArtifact；修订反馈/父版本索引使用WorkflowConversation。新浏览器会话登录后，可在同一Case历史查看，模型Key仍需本机填写。
- 人工稿另存origin=manual、引用原稿，原稿不覆盖；独立复核，不能自审/批准模拟客户。A保存的旧风险不清除；B实时重检结果另列，不伪称已更新旧记录。
- 正式知识只通过KnowledgeService读取发布版本；产品、知识、词项可草稿→独立审核→管理员发布，并保留归档/回滚历史。原8条JSON仅保留模拟/纯函数回归，未自动导入或发布。
- 产品选择支持当前发布版本及当前Case已引用的已发布历史版本；原始资料快照摘要不受归档状态变化影响。发布流程不等于产品/法规事实已被外部验证。
- 附件页读取C的AttachmentContent和定位信息；只允许当前Case的ready版本进入模型，raw/candidate不当confirmed。没有另造OCR/PDF/XLSX解析器或任意路径读取。
- 搜索通过SearchService核权/审计/保存；服务器实际搜索adapter尚未配置时明确报不可用。独立点击演示查询另存DEMO、无虚构网页URL，不静默伪装成外部背调。
- 当前Case JSON/TXT下载只读服务返回的业务稿/来源，包含origin/review状态。JSON另带当前岗位有权读取的对话索引；不导出账号库、会话token或API配置。

## 启动与首次使用

Python3.12及当前环境验证。项目根执行（独立安装环境按requirements.txt安装）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
```

在本机外层项目已有环境时，先进入team/CrossBot-B，使用../../.venv/Scripts/python.exe执行上述streamlit命令。

打开http://127.0.0.1:8502/。首次创建管理员，密码至少12位，无公开通用密码。默认无用户、不会自动建立共享账号；助手只在临时数据库测试账号。已有账号则正常登录。

管理员可导入台灯演示Case并创建/分配成员账号。角色不是权限并集，只使用当前激活角色；未授权角色灰显。客服/运营需分配Case；管理员可跨Case。复核员可审核知识但不可发布，任何提交人不能自审，管理员也不例外。

默认模型模拟，不收费；真实模式在本机填写HTTPS服务/模型/Key并勾选确认，点击才调用。Key只在本机会话，登录凭证与模型设置不进入业务request/context或导出。当前实际搜索服务及Key另待选型配置，不用模型编造搜索结果。

旧会话下载资料仍在docs/verification；不会把旧导出静默转换成V3已批准稿。已有SQLite由A迁移保留Case/对话，数据库留在忽略的data目录，不能上传或分发。

## 验证与交接

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

完整145项离线回归通过，另对最终Case历史产品选择执行12项接入回归；模型/搜索用替身，账号与数据库均在临时目录。本轮未做真实模型调用或生产账号创建。新登录/Case/权限UI尚待用户本机验收，不能用旧14项验收替代新接入验收。

[本轮接入交接](docs/b-v3-integration.md)｜[A服务说明](docs/B_V3接入说明.md)｜[公共契约](docs/contracts.md)｜[AI约束](AI开发约束.md)｜[旧英德证据及整理稿](docs/verification/v3-acceptance-20261031/README.md)。

旧面板回归入口为tests/fixtures/b_panels_app.py，仅供组件测试；产品入口固定为app.py，不能将测试面板部署为无鉴权服务。旧main.py/utils/prompts/knowledge_base只作迁移参考。

## 仍需团队完成

实际搜索adapter配置、C的上传/OCR/PDF/XLSX解析与下载、D的完整规则/W3全链继续联调。当前regex词项只保存时验语法，生成时提示需D/人工复核，不在UI线程执行任意正则。同步模型请求有超时/输出限制，不是流式实现。

人工稿的新增重检风险当前实时计算，A edit保留旧风险，尚无新风险写回参数；AI修订父版本/反馈存在受权对话索引，A单条Artifact尚无反馈字段。详见接入交接中的契约建议，不直接修改A共享模型。

本轮没有新增依赖，没有真实法规/客户成效证明，不创建hackathon-final标签，不自动合并或向客户发送消息。模型仍可能混用语言或添加内部备注，有限风险提示不能替代人工审阅。
