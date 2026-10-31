"""Snapshot installed, locked package metadata and bundled notices; no network."""
import hashlib
import json
import re
import sys
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/third-party"


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    direct = {"streamlit", "pydantic"}
    packages = []
    OUT.mkdir(parents=True, exist_ok=True)
    for line in (ROOT / "requirements-lock.txt").read_text().splitlines():
        if "==" not in line or line.startswith("#"):
            continue
        name, locked = line.strip().split("==", 1)
        dist = distribution(name)
        if dist.version != locked:
            raise ValueError(f"Version mismatch: {name}, locked={locked}, installed={dist.version}")
        slug = canonical(name)
        notices = []
        for item in dist.files or []:
            parts = Path(str(item)).parts
            idx = next((i for i, p in enumerate(parts) if p.endswith(".dist-info")), None)
            if idx is None:
                continue
            tail = Path(*parts[idx + 1:])
            if not tail.parts or (tail.parts[0].lower() != "licenses" and
                                  not any(x in tail.name.lower() for x in ("license", "copying", "notice"))):
                continue
            if ".." in tail.parts:
                raise ValueError("Unexpected package notice path")
            raw = Path(dist.locate_file(item)).read_bytes()
            dest = OUT / "licenses" / f"{slug}-{locked}" / tail
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            notices.append({"package_path": str(item).replace("\\", "/"),
                            "copy": dest.relative_to(ROOT).as_posix(),
                            "sha256": hashlib.sha256(raw).hexdigest()})
        meta = dist.metadata
        packages.append({
            "name": name, "version": locked,
            "role": "direct" if slug in direct else "locked-environment",
            "license_expression": meta.get("License-Expression"),
            "license_metadata": meta.get("License"),
            "license_classifiers": [c for c in meta.get_all("Classifier", []) if c.startswith("License")],
            "project_urls": meta.get_all("Project-URL", []), "homepage": meta.get("Home-page"),
            "requires_dist": meta.get_all("Requires-Dist", []), "notices": notices,
        })
    runtime = {"name": "CPython", "version": sys.version.split()[0], "notices": []}
    runtime_license = Path(sys.base_prefix) / "LICENSE.txt"
    if runtime_license.is_file():
        raw = runtime_license.read_bytes()
        dest = OUT / "licenses" / f"cpython-{runtime['version']}" / "LICENSE.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        runtime["notices"].append({"copy": dest.relative_to(ROOT).as_posix(),
                                   "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {"schema_version": 1, "scope": "requirements-lock.txt and local CPython license; not a complete bundled JS/native SBOM",
                "lock_sha256": hashlib.sha256((ROOT / "requirements-lock.txt").read_bytes()).hexdigest(),
                "packages": packages, "runtime": runtime}
    (OUT / "dependency-inventory.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["# 锁定环境组件清单", "", "由 tools/export_dependency_notices.py 从本机安装元数据生成；名称和版本逐项匹配锁定文件。许可证字段是上游元数据，不是完整的捆绑组件许可审计。", "",
            "| 组件 | 版本 | 范围 | 许可元数据／分类 | 随包声明文件数 |", "|---|---|---|---|---:|"]
    for p in packages:
        label = p["license_expression"] or p["license_metadata"] or "; ".join(p["license_classifiers"]) or "未提供"
        if len(label) > 180:
            label = "; ".join(p["license_classifiers"]) or "完整许可文本见原件"
        label = label.replace("\n", " ").replace("|", "\\|")
        role = "直接依赖" if p["role"] == "direct" else "锁定环境组件"
        rows.append(f"| {p['name']} | {p['version']} | {role} | {label} | {len(p['notices'])} |")
    rows += ["", "完整元数据、原件路径及SHA256见 [dependency-inventory.json](dependency-inventory.json)。许可原件位于 licenses/；streamlit若无随包原件，另见上级第三方声明中的补充来源。", "",
             "本脚本仅新增／更新当前清单与副本，不删除旧版本目录；打包时按当前清单取文件。"]
    (OUT / "dependency-inventory.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps({"packages": len(packages), "bundled_notice_files": sum(len(p['notices']) for p in packages),
                      "runtime_notices": len(runtime['notices']),
                      "missing_bundled_notices": [p['name'] for p in packages if not p['notices']]}))


if __name__ == "__main__":
    main()
