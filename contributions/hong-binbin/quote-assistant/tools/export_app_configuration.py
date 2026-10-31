"""Export review artifacts from source and an existing synthetic archive, offline."""
import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_lab.deepseek_client import Draft
from local_lab.dialogue import ChangeProposal
from local_lab.revisions import request_context


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assignments(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value.value
    body = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "body" for t in n.targets))
    params = {k.value: ast.literal_eval(v) for k, v in zip(body.keys, body.values)
              if isinstance(k, ast.Constant) and k.value not in ("model", "messages")}
    timeouts = [ast.literal_eval(k.value) for n in ast.walk(tree) if isinstance(n, ast.Call)
                for k in n.keywords if k.arg == "timeout"]
    assert timeouts == [60], "Review changed timeout configuration before exporting"
    return values, params


def main():
    out = ROOT / "deliverables/configuration"
    out.mkdir(parents=True, exist_ok=True)
    generation, generation_params = assignments(ROOT / "local_lab/deepseek_client.py")
    dialogue, dialogue_params = assignments(ROOT / "local_lab/dialogue.py")
    for name, text in [("generation-system-prompt.txt", generation["SYSTEM_PROMPT"]),
                       ("interpretation-system-prompt.txt", dialogue["PROMPT"])]:
        (out / name).write_text(text, encoding="utf-8")
    source_paths = ["start-local.cmd", "requirements-lock.txt", "local_lab/app.py",
                    "local_lab/deepseek_client.py", "local_lab/dialogue.py", "local_lab/dialogue_panel.py",
                    "local_lab/revisions.py", "local_lab/revision_panel.py", "local_lab/history_recovery.py",
                    "local_lab/rfq_check.py", "local_lab/check_report.py", "local_lab/payment_ratio.py",
                    "local_lab/payment_wording.py", "local_lab/catalog.py", "local_lab/quotation.py",
                    "planning/fixtures/products.json", "local_lab/rules/rfq-material.json",
                    "local_lab/rules/payment-wording.json"]
    snapshot = {
        "schema_version": 1,
        "purpose": "Review snapshot; not loaded by the app and not a hosted-platform import format",
        "configuration_basis": "Current local source, not a capture of a running session",
        "entrypoint": "local_lab/app.py", "launcher": "start-local.cmd",
        "endpoint": generation["ENDPOINT"], "model": generation["MODEL"],
        "credential_handling": "Entered by user in local masked UI; credential value is not read or exported",
        "generation": {"parameters": generation_params, "timeout_seconds": 60,
                       "prompt_file": "generation-system-prompt.txt", "output_schema": Draft.model_json_schema()},
        "interpretation": {"parameters": dialogue_params, "timeout_seconds": 60,
                           "prompt_file": "interpretation-system-prompt.txt", "output_schema": ChangeProposal.model_json_schema()},
        "sources": [{"path": p, "sha256": digest(ROOT / p)} for p in source_paths],
    }
    (out / "app-configuration.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = ROOT / "docs/verification/demo-two-rounds-history.json"
    records = json.loads(archive.read_text(encoding="utf-8-sig"))["versions"]
    assert [r["version"] for r in records] == [1, 2, 3]
    examples = {
        "notice": "Offline reconstruction using current request_context and saved synthetic records. Not original HTTP logs; no model request was made. No credentials included.",
        "archive": archive.relative_to(ROOT).as_posix(), "archive_sha256": digest(archive),
        "request_context_source_sha256": digest(ROOT / "local_lab/revisions.py"),
        "examples": [{"target_version": record["version"],
                      "generation_user_payload": request_context(record["context"], records[i-1] if i else None,
                                                                 record.get("check_report"), record.get("platform_report"))}
                     for i, record in enumerate(records)],
    }
    (out / "reconstructed-generation-inputs.json").write_text(json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"exported_files": 4, "source_hashes": len(source_paths),
                      "reconstructed_examples": len(examples["examples"]), "network_calls": 0}))


if __name__ == "__main__":
    main()
