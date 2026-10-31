# TokenHubCloud 接入与 403 排查

日期：2026-10-31。接口地址与协议取自用户提供的平台接入说明。

## 本轮定位

同一台机器、同一公开 `GET https://api.tokenhubcloud.net/v1/models`，均不带密钥：

| 请求标识 | 返回 |
|---|---|
| Python urllib 默认 User-Agent | HTTP 403，文本 `error code: 1010` |
| 明确的 `CrossBot/1.0` User-Agent | HTTP 401，JSON `API_KEY_REQUIRED` |

因此原先“服务拒绝访问，请核对账户权限”不能直接归因为账号权限：
网关在鉴权之前就可能拦截默认 Python 客户端。本轮为请求增加真实应用标识，
不伪装浏览器、不关闭 TLS 校验、不跟随携带密钥的重定向。
修复后的 `POST /v1/responses` 在无密钥时也返回正常的 HTTP 401 JSON。
这些探测未使用真实密钥，不能证明账号额度、模型授权或真实生成已经成功。

## 页面填写

1. 在 W1/W2 侧栏选择“真实”。新会话默认使用 `https://api.tokenhubcloud.net`。
2. 接口协议选“自动”或“Responses（推荐）”。最终接口应显示为
   `https://api.tokenhubcloud.net/v1/responses`。
3. 模型保留 `gpt-6.1-sol`，但必须在自己的控制台核对该精确模型 ID 是否有权限。
   用户提供的平台文档示例包含 `gpt-5.6-sol`，未证明 `gpt-6.1-sol` 对当前分组可用。
4. 仅在本机 API Key 密码框输入该平台的密钥，再确认资料发送和额度。
5. 点击“测试连接（消耗少量额度）”。只发送固定的 `Reply with OK only.`，
   不发送 Case、产品、历史对话，不新增业务草稿。成功仅代表基础文本调用可用。
6. 测试成功后再运行 W1 首版、修订和 W2；W2 的 JSON Schema 支持仍需真实验收。

旧浏览器会话保留之前输入的地址。请手动核对地址，不要把本网关密钥填到
`api.openai.com`。修改地址会清空密钥和发送确认；修改模型或协议会取消发送确认。

## 地址和协议规则

- 本平台根地址、`/v1` 基础地址、完整 `/v1/responses` 均可，避免重复拼接路径。
- TokenHubCloud 和 OpenAI 官方主机自动选 Responses；其他主机默认保留 Chat Completions。
- 可明确选 Chat Completions，平台根地址会解析到 `/v1/chat/completions`。
- 完整接口路径与手动所选协议冲突时，在发送前报错，不静默改协议或重复付费请求。
- `/keys` 是管理页面，`/login` 是登录页，`/v1/messages` 是 Claude 协议，均不作为本客户端基础地址。
- 公共配置新增可选 `AppConfig.model_api_protocol`：`auto`（默认）、`responses`、
  `chat_completions`。新增字段放在末尾，保留原参数顺序；环境配置可使用 `MODEL_API_PROTOCOL`。
  正式页面仍只使用本机会话设置，不加载 `.env`。

## 错误处理

页面现在显示 HTTP 状态码；不输出原始服务错误体、请求头或密钥。
401 优先检查密钥和服务是否匹配；403 还需检查平台网关、密钥分组与模型权限；
404 检查接口和模型 ID；429 检查限额；超时后由用户决定是否手动重试。
程序不自动切模型、自动换协议或降级为模拟，不把失败结果保存为业务稿。

官方 Responses 请求及响应结构依据：
[Create a model response](https://developers.openai.com/api/reference/python/resources/responses/methods/create)。
网关地址和账号权限以 [TokenHubCloud](https://api.tokenhubcloud.net) 控制台为准。

## 本轮验证

全量回归 653 项：649 项通过、4 项跳过，0 失败、0 错误。
新默认地址调整后，客户端、设置页和配置契约的 29 项定向回归再次全部通过。
相关 Python 语法检查与 `git diff --check` 通过。测试使用替身和合成数据；
真实网络探测仅为上述无密钥请求，没有运行真实账号的付费生成，尚未提交或推送。
