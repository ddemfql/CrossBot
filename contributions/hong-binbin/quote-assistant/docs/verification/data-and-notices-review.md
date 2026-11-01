# 数据来源与第三方声明核对

日期：2026-10-30。用户批准整理数据来源和第三方组件声明。

## 交付文件

- docs/data-sources.md：模拟目录、12个规划用例、用户输入、模型导出、赛事截图来源；规则依据、时效、构建调用方式及数据处理说明。
- THIRD_PARTY_NOTICES.md：直接依赖、CPython、锁定环境、外部模型服务、AI辅助开发及许可范围。
- docs/third-party/dependency-inventory.md / .json：39个锁定组件及安装元数据、原件路径和校验值。
- docs/third-party/licenses/：78份随Python分发包附带声明，加1份CPython许可，均原样复制。
- tools/export_dependency_notices.py：本地清单生成脚本，无网络、无模型调用。
- docs/verification/data-source-manifest.json：12份数据、规则、源码依据及证据文件的SHA256快照。

## 验证结果

- 39个已安装版本全部与requirements-lock.txt一致；2个直接依赖为Streamlit和Pydantic。
- 79份复制文件逐项SHA256校验通过；清单中的副本存在。15个已生成文档本地链接检查通过；本检查文件链接随后补齐。
- products.json含5款商品，cases.json含12个规划用例；不声称这些规划用例已全部通过真实模型评测。
- 阿里2016公开页本次可读，当前规则中心仍只有导航框架；联合国摘要PDF直接读取403。保留旧参考边界，没有改写运行时规则或补造核实状态。
- Streamlit安装文件清单未含许可原件；元数据为Apache-2.0，官方develop/LICENSE可读。GitHub提交API请求因403限流失败，未生成固定提交副本，声明中如实记录。
- 初次打印个别许可正文遇到Windows GBK编码错误；随后通过文件原始字节复制与SHA256核对完成，不影响原件保存。
- 未改业务代码、原始数据、规则及演示导出；未安装依赖、重启服务、读取密钥、调用模型或上传材料。不需要为本次声明重跑既有业务测试。

本清单不是全部JS／原生捆绑组件的完整SBOM。当前不分发.venv；若未来捆绑分发，需按实际内容补充Streamlit安装版和捆绑声明。未给项目源码擅自选择开源许可证。

下一步：整理平台配置与调用材料，再制作海报及3分钟讲稿。
