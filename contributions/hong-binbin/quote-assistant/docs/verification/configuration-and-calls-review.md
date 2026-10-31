# 配置与调用材料检查

日期：2026-10-30。用户批准整理配置与调用说明。

## 产物

- docs/configuration-and-calls.md：配置位置、角色与输出、两工作流、请求与返回结构、已核实三版结果、异常与恢复。
- deliverables/configuration/app-configuration.json：从当前源码导出的参数、Pydantic输出结构、18份源文件SHA256。
- deliverables/configuration/generation-system-prompt.txt、interpretation-system-prompt.txt：当前源码提示词原值。
- deliverables/configuration/reconstructed-generation-inputs.json：当前组装函数加既有模拟归档重建的三个生成输入。明确不是历史HTTP日志。
- tools/export_app_configuration.py：离线重建上述四份配置材料的脚本。

## 本轮实际检查

- 18份源文件SHA与快照一致；两份提示词文本与源码AST常量完全相同。
- 生成1800／理解700 tokens、各自JSON格式参数由源码body字典提取；输出schema来自实际Pydantic模型。
- 三个重建输入的金额分别600／1050／1050 USD；均保留P01 mismatch，V2/V3有dialogue及正确上一版号。
- 原始demo-two-rounds-history.json的SHA256仍为8613ade8325c777eaf673429165d32b80d94d5c0c9479a9d4e8299d6892c408f，没有修改。
- 两份JSON递归检查没有api_key、deepseek_api_key、Authorization、access_token字段；脚本未读取任何用户会话或凭证。提示词也是应用业务指令，非用户密钥。
- 没有发起模型请求、读取用户页面或重启服务；没有改变业务代码。未重跑无关业务测试，也没有新增真实请求日志。

当前配置快照是审阅材料，应用不加载它，不是其他智能体平台的可导入包。早期版本实际提示词、HTTP响应、request ID、用量和耗时日志没有完整归档，不用重建材料替代这些证据。

下一步准备项目海报及3分钟讲稿，最终完整工程与tag仍需收尾。
