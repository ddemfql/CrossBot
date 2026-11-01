# B第六步：已有业务记录JSON/TXT下载

2026-10-31｜feature/w1-w2-platform。用户报告W1/W2真实首版和W1两轮真实修订通过，批准先保存证据。

## 文件与行为

- workflows/exports.py：只读取sales_conversation、listing_request、listing_results三个已有成功记录键；固定字段白名单序列化core请求/响应与风险。绝不转储整个session_state、AppConfig或模型设置。
- workflows/export_ui.py：当前页面底部提供「下载全部业务记录（JSON）」「下载W1全部版本（TXT）」「下载W2结果（TXT）」。TXT使用UTF-8 BOM。下载on_click=ignore，无模型调用，不修改业务状态。
- app.py仅新增导出模块调用；不修改当前conversation数据类，不重载业务模块、不重启服务。已有用户会话在脚本重跑后仍可使用原对象。
- tests/test_exports.py新增7项测试，验证V1/V2/V3、两轮反馈、逐版原文/引用、real/mock/模型、平台、快照与状态隔离、白名单不读设置、空/不一致状态及已有会话无额外生成。

## 导出结构与证据边界

JSON格式标识crossbot-business-export，schema_version=1；w1包含保存的原始业务请求和versions（编号按现有顺序、kind、feedback、response）；w2包含保存的请求及各平台结果。输入框尚未生成的新改动不替换已保存输入，模式切换不改写原版来源。

exported_at_utc仅为导出时间。现有历史没有各版生成时间、原始HTTP请求ID或用量，不补造。business_sha256是业务快照规范JSON的完整性摘要，不是服务真实性签名；不能据此声称所有输出事实正确。

旧版本正文和引用保持原样，导出不重新检索、不重算业务资料、不调模型。新字段只描述导出格式，不修改core公共契约；无需队友同步契约。各记录的未知provider/model仍标注未知，不伪造real。

## 验证

团队根执行 `../../.venv/Scripts/python.exe -B -m unittest discover -s tests -q`，共69项通过（此前62+导出7）。py_compile及git diff --check通过。AppTest预置三版real标记的虚拟业务记录和虚拟密钥，加载下载控件后历史及密钥保持、真实客户端被拦截且未调用。测试不是用户真实会话的独立核验。

新增下载不涉及SDK或网络，未新增依赖、未commit/push、未停止8502服务。用户尚需实际下载才能持久保存；不代表自动备份或支持导入恢复。

## 用户验收

保持当前8502页面和程序运行，向下滚动至「备份与下载」。若提示源码改变，使用Streamlit Rerun/重新运行使同一会话载入新控件，不刷新浏览器或重启程序。

先核对显示「已保存W1：3版」及W2平台Amazon；若不符，先保存当前可用记录并反馈，不重复付费生成来凑版本。下载三个文件：

- crossbot-business-history.json
- crossbot-w1-history.txt
- crossbot-w2-results.txt

文件选择项目外层downloads目录保存；浏览器默认目录未由助手验证为已修改。下载后再检查JSON的w1.versions是否含3版和两轮feedback、w2是否含Amazon，以及各版real标记和事实。当前下载保存记录并非原始网络日志，真实输出须另行读取文件核实。

## 限制

该格式尚无任意导入、自动持久化、签名或加密。只排除配置/密钥字段；用户若自行把敏感内容写入业务文本，该原文也会随业务导出，应自行核对分享范围。全体知识仍自建模拟资料，有限规则与模型提示不能保证没有事实错误。
