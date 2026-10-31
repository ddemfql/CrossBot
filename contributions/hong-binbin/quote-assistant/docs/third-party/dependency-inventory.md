# 锁定环境组件清单

由 tools/export_dependency_notices.py 从本机安装元数据生成；名称和版本逐项匹配锁定文件。许可证字段是上游元数据，不是完整的捆绑组件许可审计。

| 组件 | 版本 | 范围 | 许可元数据／分类 | 随包声明文件数 |
|---|---|---|---|---:|
| altair | 6.3.0 | 锁定环境组件 | License :: OSI Approved :: BSD License | 1 |
| annotated-types | 0.8.0 | 锁定环境组件 | MIT | 1 |
| anyio | 4.15.1 | 锁定环境组件 | MIT | 1 |
| attrs | 26.1.0 | 锁定环境组件 | MIT | 1 |
| certifi | 2026.10.23 | 锁定环境组件 | MPL-2.0 | 1 |
| charset-normalizer | 3.5.2 | 锁定环境组件 | MIT | 1 |
| click | 8.5.0 | 锁定环境组件 | BSD-3-Clause | 1 |
| h11 | 0.16.0 | 锁定环境组件 | MIT | 1 |
| idna | 3.20 | 锁定环境组件 | BSD-3-Clause | 1 |
| itsdangerous | 2.2.0 | 锁定环境组件 | License :: OSI Approved :: BSD License | 1 |
| Jinja2 | 3.1.6 | 锁定环境组件 | License :: OSI Approved :: BSD License | 1 |
| jsonschema | 4.26.0 | 锁定环境组件 | MIT | 1 |
| jsonschema-specifications | 2025.9.1 | 锁定环境组件 | MIT | 1 |
| MarkupSafe | 3.0.4 | 锁定环境组件 | BSD-3-Clause | 1 |
| narwhals | 2.27.1 | 锁定环境组件 | MIT | 1 |
| numpy | 2.5.4 | 锁定环境组件 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | 17 |
| packaging | 26.3 | 锁定环境组件 | Apache-2.0 OR BSD-2-Clause | 3 |
| pandas | 3.1.0 | 锁定环境组件 | BSD-3-Clause | 19 |
| pillow | 13.0.0 | 锁定环境组件 | MIT-CMU | 1 |
| protobuf | 7.36.2 | 锁定环境组件 | 3-Clause BSD License | 1 |
| pyarrow | 26.0.0 | 锁定环境组件 | Apache-2.0 | 2 |
| pydantic | 2.14.1 | 直接依赖 | MIT | 1 |
| pydantic_core | 2.50.1 | 锁定环境组件 | MIT | 1 |
| pydeck | 0.9.3 | 锁定环境组件 | Apache License 2.0 | 1 |
| python-dateutil | 3.0.0 | 锁定环境组件 | Dual License | 1 |
| python-multipart | 0.0.32 | 锁定环境组件 | Apache-2.0 | 1 |
| referencing | 0.37.0 | 锁定环境组件 | MIT | 1 |
| requests | 2.34.2 | 锁定环境组件 | Apache-2.0 | 2 |
| rpds-py | 2026.9.1 | 锁定环境组件 | MIT | 1 |
| six | 1.17.0 | 锁定环境组件 | MIT | 1 |
| starlette | 1.10.0 | 锁定环境组件 | BSD-3-Clause | 1 |
| streamlit | 1.66.1 | 直接依赖 | Apache-2.0 | 0 |
| typing-inspection | 0.4.4 | 锁定环境组件 | MIT | 1 |
| typing_extensions | 4.16.0 | 锁定环境组件 | PSF-2.0 | 1 |
| tzdata | 2026.5 | 锁定环境组件 | Apache-2.0 | 2 |
| urllib3 | 2.8.0 | 锁定环境组件 | MIT | 1 |
| uvicorn | 0.54.0 | 锁定环境组件 | BSD-3-Clause | 1 |
| watchdog | 6.0.0 | 锁定环境组件 | Apache-2.0 | 2 |
| websockets | 17.3 | 锁定环境组件 | BSD-3-Clause | 1 |

完整元数据、原件路径及SHA256见 [dependency-inventory.json](dependency-inventory.json)。许可原件位于 licenses/；streamlit若无随包原件，另见上级第三方声明中的补充来源。

本脚本仅新增／更新当前清单与副本，不删除旧版本目录；打包时按当前清单取文件。
