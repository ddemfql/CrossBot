# Third-party components and services

CrossBot root application; snapshot date: 2026-11-01. Third-party libraries, OCR models, fonts and hosted services are not claimed as original CrossBot work.

## Inventory scope

This inventory was extracted with `importlib.metadata` from the independently installed and verified Linux / CPython 3.12.16 environment. It covers 64 installed Python distributions: 11 root dependencies, 52 transitive dependencies, and 1 environment tools or extras. Versions are a verification snapshot, not a promise that range-based `requirements.txt` will resolve identically later.

The [machine-readable inventory](docs/submission/third-party-components.json) preserves distribution names, exact versions, original license fields, license classifiers, upstream URLs, `Requires-Dist` declarations, and paths to available installed LICENSE/NOTICE files. Those file paths refer to the installed environment; this source submission does not copy those license files. SPDX expressions are normalized only from an upstream declared field accepted by `packaging.licenses`; unspecified BSD variants, absent license fields and other ambiguous declarations remain unresolved. No network lookup was used to fill gaps.

## Direct root dependencies

| Distribution | Verified version | License declaration | Homepage / source |
| --- | --- | --- | --- |
| openai | 3.42.0 | Apache-2.0 | [upstream](https://github.com/openai/openai-python) |
| openpyxl | 3.1.5 | MIT | [upstream](https://openpyxl.readthedocs.io) |
| pypdf | 6.21.4 | BSD-3-Clause | [upstream](https://github.com/py-pdf/pypdf) |
| pypdfium2 | 4.30.0 | (Apache-2.0 OR BSD-3-Clause) AND LicenseRef-PdfiumThirdParty | [upstream](https://github.com/pypdfium2-team/pypdfium2) |
| python-docx | 1.2.0 | MIT | [upstream](https://github.com/python-openxml/python-docx) |
| python-dotenv | 1.2.4 | BSD-3-Clause | [upstream](https://github.com/theskumar/python-dotenv) |
| rapidocr-onnxruntime | 1.4.4 | Apache-2.0 | [upstream](https://github.com/RapidAI/RapidOCR) |
| reportlab | 4.5.1 | BSD license (see license.txt for details), Copyright (c) 2000-2025, ReportLab Inc. (SPDX unresolved) | [upstream](https://www.reportlab.com/) |
| streamlit | 1.66.1 | Apache-2.0 | [upstream](https://streamlit.io) |
| xlrd | 2.0.2 | BSD (SPDX unresolved) | [upstream](http://www.python-excel.org/) |
| xlwt | 1.3.0 | BSD (SPDX unresolved) | [upstream](http://www.python-excel.org/) |

## Transitive dependencies

| Distribution | Verified version | License declaration | Homepage / source |
| --- | --- | --- | --- |
| altair | 6.3.0 | License :: OSI Approved :: BSD License; SPDX unresolved | [upstream](https://github.com/vega/altair) |
| annotated-types | 0.8.0 | MIT | [upstream](https://github.com/annotated-types/annotated-types) |
| anyio | 4.15.1 | MIT | [upstream](https://github.com/agronholm/anyio) |
| attrs | 26.1.0 | MIT | Not declared |
| certifi | 2026.10.23 | MPL-2.0 | [upstream](https://github.com/certifi/python-certifi) |
| charset-normalizer | 3.5.2 | MIT | Not declared |
| click | 8.5.0 | BSD-3-Clause | [upstream](https://github.com/pallets/click/) |
| et_xmlfile | 2.0.0 | MIT | [upstream](https://foss.heptapod.net/openpyxl/et_xmlfile) |
| flatbuffers | 25.12.19 | Apache 2.0 (SPDX unresolved) | [upstream](https://google.github.io/flatbuffers/) |
| h11 | 0.16.0 | MIT | [upstream](https://github.com/python-hyper/h11) |
| httpcore2 | 2.16.0 | BSD-3-Clause | [upstream](https://github.com/pydantic/httpx2) |
| httpx2 | 2.16.0 | BSD-3-Clause | [upstream](https://github.com/pydantic/httpx2) |
| idna | 3.20 | BSD-3-Clause | [upstream](https://github.com/kjd/idna) |
| itsdangerous | 2.2.0 | License :: OSI Approved :: BSD License; SPDX unresolved | [upstream](https://github.com/pallets/itsdangerous/) |
| Jinja2 | 3.1.6 | License :: OSI Approved :: BSD License; SPDX unresolved | [upstream](https://github.com/pallets/jinja/) |
| jiter | 0.18.0 | MIT | [upstream](https://github.com/pydantic/jiter/) |
| jsonschema | 4.26.0 | MIT | [upstream](https://github.com/python-jsonschema/jsonschema) |
| jsonschema-specifications | 2025.9.1 | MIT | [upstream](https://github.com/python-jsonschema/jsonschema-specifications) |
| lxml | 6.1.3 | BSD-3-Clause | [upstream](https://lxml.de/) |
| MarkupSafe | 3.0.4 | BSD-3-Clause | [upstream](https://github.com/pallets/markupsafe/) |
| narwhals | 2.28.0 | MIT | [upstream](https://github.com/narwhals-dev/narwhals) |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | [upstream](https://numpy.org) |
| onnxruntime | 1.31.1 | MIT License (SPDX unresolved) | [upstream](https://onnxruntime.ai) |
| opencv-python | 5.0.0.93 | Apache 2.0 (SPDX unresolved) | [upstream](https://github.com/opencv/opencv-python) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [upstream](https://github.com/pypa/packaging) |
| pandas | 3.1.0 | BSD-3-Clause | [upstream](https://pandas.pydata.org) |
| pillow | 13.0.0 | MIT-CMU | [upstream](https://python-pillow.org) |
| protobuf | 7.36.2 | 3-Clause BSD License (SPDX unresolved) | [upstream](https://developers.google.com/protocol-buffers/) |
| pyarrow | 26.0.0 | Apache-2.0 | [upstream](https://arrow.apache.org/) |
| pyclipper | 1.4.0 | MIT | [upstream](https://github.com/fonttools/pyclipper) |
| pydantic | 2.14.2 | MIT | [upstream](https://github.com/pydantic/pydantic) |
| pydantic_core | 2.50.2 | MIT | [upstream](https://github.com/pydantic/pydantic) |
| pydeck | 0.9.3 | Apache License 2.0 (SPDX unresolved) | [upstream](https://github.com/visgl/deck.gl/tree/master/bindings/pydeck) |
| python-dateutil | 3.0.0 | Dual License (SPDX unresolved) | [upstream](https://github.com/dateutil/dateutil) |
| python-multipart | 0.0.33 | Apache-2.0 | [upstream](https://github.com/Kludex/python-multipart) |
| PyYAML | 6.0.3 | MIT | [upstream](https://pyyaml.org/) |
| referencing | 0.37.0 | MIT | [upstream](https://github.com/python-jsonschema/referencing) |
| requests | 2.34.2 | Apache-2.0 | [upstream](https://github.com/psf/requests) |
| rpds-py | 2026.9.1 | MIT | [upstream](https://github.com/crate-py/rpds) |
| shapely | 2.2.0 | BSD-3-Clause | [upstream](https://github.com/shapely/shapely) |
| six | 1.17.0 | MIT | [upstream](https://github.com/benjaminp/six) |
| sniffio | 1.3.1 | MIT OR Apache-2.0 | [upstream](https://github.com/python-trio/sniffio) |
| starlette | 1.10.0 | BSD-3-Clause | [upstream](https://github.com/Kludex/starlette) |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | [upstream](https://tqdm.github.io) |
| truststore | 0.10.5 | MIT | [upstream](https://github.com/sethmlarson/truststore) |
| typing_extensions | 4.16.0 | PSF-2.0 | [upstream](https://github.com/python/typing_extensions) |
| typing-inspection | 0.4.4 | MIT | [upstream](https://github.com/pydantic/typing-inspection) |
| tzdata | 2026.6 | Apache-2.0 | [upstream](https://github.com/python/tzdata) |
| urllib3 | 2.8.0 | MIT | Not declared |
| uvicorn | 0.54.0 | BSD-3-Clause | [upstream](https://uvicorn.dev/) |
| watchdog | 6.0.0 | Apache-2.0 | [upstream](https://github.com/gorakhargosh/watchdog) |
| websockets | 17.4 | BSD-3-Clause | [upstream](https://github.com/python-websockets/websockets) |

## Environment tools or extras

| Distribution | Verified version | License declaration | Homepage / source |
| --- | --- | --- | --- |
| pip | 26.2.2 | MIT | [upstream](https://pip.pypa.io/) |

These additional installed distributions are recorded for completeness. They are not declared application dependencies unless the root requirements or a selected dependency explicitly requires them.

## Redistribution, OCR and native libraries

This delivery contains project source, configuration examples, documentation and the user-authorized transaction example ZIP. It does not bundle a Python interpreter, virtual environment or Python dependency binaries. Recipients install dependencies themselves and should retain the actual distributions' license and notice files. This document does not relicense third-party components or the example transaction material.

`rapidocr-onnxruntime` 1.4.4 installs the following model files in its distribution: `ch_PP-OCRv4_det_infer.onnx`, `ch_PP-OCRv4_rec_infer.onnx`, and `ch_ppocr_mobile_v2.0_cls_infer.onnx`. They are used locally for OCR after installation; these weights are not included in the source delivery. The package metadata declares Apache-2.0, but this inventory has not independently established each weight's provenance or a separate model license. A future bundled model distribution requires checking its exact upstream model notices.

The Python metadata list is not a complete inventory of bundled native libraries or browser JavaScript. In particular, PDFium, ONNX Runtime, OpenCV, NumPy and other wheels can contain their own third-party code. For the actual installed version, inspect all applicable notices, including `pypdfium2-4.30.0.dist-info/LicenseRef-PdfiumThirdParty.txt`, `onnxruntime/ThirdPartyNotices.txt`, and `cv2/LICENSE-3RD-PARTY.txt`. The pypdfium2 metadata explicitly includes a `LicenseRef-PdfiumThirdParty` obligation; its declaration must not be reduced to Apache or BSD alone.

## Runtime and local fonts

CPython 3.12.16 and its standard library, including SQLite integration, are external runtime components, not included binaries. Preserve the runtime distribution's own license and bundled component notices when redistributing it. Operating-system libraries installed through apt and Windows OCR language capabilities are outside this Python metadata inventory.

PDF generation may use the user's installed Windows SimSun or Ubuntu UMing font; fallback STSong-Light uses the reader's font support. No system font file is copied into this repository or source package. Installing or embedding a font in an exported PDF remains subject to that font's actual license; availability on a computer does not establish redistribution rights.

## External model services and development tools

W1/W2 use an independently configured HTTPS model service through the OpenAI Python SDK. The prefilled TokenHubCloud address is an external provider; accounts, keys, quota, service terms and provider/model permissions are supplied by the user. No hosted model weights are downloaded or redistributed by this project. SDK licensing does not establish a hosted model's license or output rights. This inventory does not make a real API call or validate service availability.

Codex was used as development assistance for code/document review and delivery preparation. It is not an application runtime dependency. Version control, Python installation tools and the operating system are external development/runtime prerequisites.

## Included contribution subproject

`contributions/hong-binbin/quote-assistant/` has its own dependency inputs, lockfile and [third-party notice](contributions/hong-binbin/quote-assistant/THIRD_PARTY_NOTICES.md), with a separate inventory and license copies under its `docs/third-party/`. Those records concern that subproject's environment and are retained as-is; they do not describe the root CrossBot environment or override this inventory. Root installation instructions use the root `requirements.txt`.

## Verification and known gaps

The inventory's active dependency closure was evaluated against this environment's markers with no optional extras. All direct installed versions were checked against the root requirements, JSON was parsed again, component names were checked for uniqueness, and `pip check` was run on the verified environment. Source metadata can omit license files or use broad legacy labels; these entries are preserved rather than guessed. This is not a complete SPDX/CycloneDX SBOM or a legal review. If binaries, fonts or OCR models are bundled later, or dependency versions change, regenerate the inventory and collect the corresponding full upstream notices for the actual artifacts.
