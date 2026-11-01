"""Versioned local demo wording rules; no official-policy or semantic verdicts."""
import hashlib
import json
from pathlib import Path
import re

from core.errors import ParseError

RULE_FILE = Path(__file__).with_name("prohibited_terms.json")


def load_wording_rules():
    try:
        raw = RULE_FILE.read_bytes()
        data = json.loads(raw)
        if (data["schema_version"] != 1 or data["data_kind"] != "synthetic"
                or data["source"]["official"] is not False
                or not isinstance(data["rules"], list) or not data["rules"]):
            raise ValueError
        for key in ("version",):
            if not isinstance(data[key], str) or not data[key].strip():
                raise ValueError
        for key in ("title", "path", "kind"):
            if not isinstance(data["source"][key], str) or not data["source"][key].strip():
                raise ValueError
        if data["source"]["kind"] != "synthetic":
            raise ValueError
        ids = set()
        for rule in data["rules"]:
            for key in ("id", "code", "pattern", "message", "replacement"):
                if not isinstance(rule[key], str) or not rule[key].strip():
                    raise ValueError
            if rule["id"] in ids:
                raise ValueError
            ids.add(rule["id"])
            if rule["severity"] not in {"warning", "blocking"}:
                raise ValueError
            re.compile(rule["pattern"], re.I)
    except (OSError, KeyError, TypeError, ValueError, re.error):
        raise ParseError("演示风险词库缺失或格式错误，请修复后再生成；未跳过词项检查。") from None
    return data, hashlib.sha256(raw).hexdigest()
