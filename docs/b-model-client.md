# B第五步：统一模型客户端与模式开关

2026-10-31｜feature/w1-w2-platform。用户已确认前四步，并批准本步实现。真实调用尚待用户手动验证。

## 修改与公共契约

新增providers/llm_client.py、settings_ui.py，app.py侧栏提供模拟/真实开关。W1/W2共用HttpChatClient；W1请求文本，W2请求JSON并继续严格校验返回的平台集合。使用原core.config.AppConfig、core.errors及GenerationResponse.provider/model，不改core、不增加依赖，采用标准库urllib而非新增SDK。旧.env和旧utils客户端不加载。

模拟默认开启，无请求；真实须本机填Key/HTTPS地址/模型并确认后才能点生成。换地址清除密钥和确认，清除密钥不删除已有历史。每版保存自身mock/real和模型标记；切换模式不会把旧模拟版伪装成真实版，W1须重新首版后才在新模式修订。

## 请求和错误边界

- 基础地址可带/v1或完整/chat/completions；只允许HTTPS、不含userinfo/query/fragment。禁重定向，不关闭TLS校验。API密钥不要求sk-前缀，仅非空ASCII且无空白；不记录/打印/写入文件或结果。
- 非流式；默认网络等待60秒（网络等待设置，不是绝对端到端耗时保证）。W1最多1800、W2最多4096输出tokens；请求及返回限制1MiB；拒绝空内容、错误结构和finish_reason非stop的截断结果。
- DeepSeek官方主机发送thinking disabled，W2设置response_format json_object；其他兼容主机不发送DeepSeek专有thinking参数。提供商兼容性仍需实际验证。
- 认证/权限、余额、限流、参数、超时和连接故障映射至公共错误，显示固定信息，不显示原始错误体或请求头。
- 合理重试策略为无自动重试（最多一次发送），对暂时错误标注retryable，用户可稍后手动再次触发。避免超时后隐式重复付费；格式检查失败也可能已经发生服务费用。没有静默降级为模拟。
- 成功且结构有效才替换结果；失败保留W1旧历史、W2旧批次，不把错误正文当商务草稿。结构通过不保证事实正确、目标语言正确或合规通过。

## 官方配置依据（2026-10-31核对）

- [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)：/chat/completions及非思考参数；默认服务https://api.deepseek.com。
- [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)：response_format=json_object，提示中明确JSON及形状，注意空白与截断；当前示例模型deepseek-flash。
- [错误码](https://api-docs.deepseek.com/quick_start/error_codes/)：401认证、402余额、429限流、500/503服务问题。

界面已填上述官方默认地址与模型。其他服务的Key、地址和模型应互相对应，不把DeepSeek Key填到其他服务。当前程序不自动发现或核验账户模型权限。

## 验证及交接

在团队根运行`../../.venv/Scripts/python.exe -B -m unittest discover -s tests -q`，62项通过（此前51+HTTP客户端7+页面4）。测试拦截网络，使用明确虚拟测试token；包括配置拦截、JSON/文本参数、任意Key前缀、跳转拒绝、HTTP错误分类、超时/连接、截断/空/超大响应、清密钥/换地址、真实来源标记、失败历史保留及默认模拟不联网。

相关py_compile和git diff --check通过。实际网络调用0，无.env读取，未安装依赖，未commit/push/创建PR；无需其他分支同步core契约。8502需重启载入模块，历史/密钥仅在会话内存，重启会重置。已有旧服务停止时出现Windows连接清理异常，不宣称根因修复。

## 用户第一次真实验收

打开8502，侧栏模型模式选真实，默认DeepSeek官方地址及deepseek-flash；仅在本机密码框填自己的Key并确认发送资料/费用。先选W1英文，保留DEMO-LAMP-001和少量模拟产品文字，只点一次「生成首版（真实，消耗额度）」。

应出现来源real与模型名、英文回复及待确认/引用。人工核对：没有编造认证、价格、交期；知识仍全部模拟。失败时记录页面显示的错误类型即可，不复制密钥或账号信息到聊天。成功后回报W1真实生成通过，再决定W2和两轮真实修订的后续验收，避免一次发送多次请求。

本阶段完成的是客户端与离线验证，不把fake响应测试记为真实API成功。正式来源、完整事实/规则检查、语义修订的真实质量、流式事件、持久化及导出仍有限或未实现。

## OpenAI GPT API 接入（2026-10-31）

当前设置页默认 `https://api.openai.com/v1` 和 `gpt-6.1-sol`。识别到 OpenAI 官方 API 主机时走 Responses API：将 system/developer 内容发为 `instructions`，其余对话以 `input` 传递，设置 `store=false`，并从全部 `output_text` 内容中读取完整结果。W1 使用文本输出；W2 在已选平台集合可解析时构造 `strict: true` 的 JSON Schema，否则 JSON 工作流使用 JSON Object 模式。Responses 状态非 completed、输出为空/畸形或超限均拒绝保存。

其他兼容服务仍走原 Chat Completions 路径；DeepSeek 的 `thinking` 参数只发给 DeepSeek 官方主机。OpenAI API Key 在本机页面输入，由 Streamlit 服务端会话内存持有；不读取 `.env`、不写入数据库或代码。API 组织的额度与费用依账户配置和当前价格表为准。本轮只做离线传输替身测试，真实网络调用需由用户在本机配置密钥后手动验收。

官方依据： [Responses 文本生成](https://developers.openai.com/api/docs/guides/text?api-mode=responses)、[迁移至 Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)。模型/请求行为以该文档和用户账户权限为准。
