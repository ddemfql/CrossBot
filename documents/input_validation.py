"""Shared safe-text boundary for entered values and legacy output snapshots."""
from dataclasses import fields, is_dataclass
import re
from core.errors import ValidationError

_UNSAFE_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def validate_input_text(value):
    if isinstance(value, str):
        if _UNSAFE_XML.search(value):
            raise ValidationError("填写内容包含不可导出的控制字符，请移除后重新保存。")
    elif isinstance(value, dict):
        for key, item in value.items():
            validate_input_text(key)
            validate_input_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            validate_input_text(item)
    elif is_dataclass(value):
        for field in fields(value):
            validate_input_text(getattr(value, field.name))

