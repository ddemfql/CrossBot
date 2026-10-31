"""Typed snapshot deserialization paired with core.models.to_dict."""
from __future__ import annotations
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

def from_dict(model, value):
    if value is None or model is Any:
        return value
    origin, args = get_origin(model), get_args(model)
    if origin in (Union, UnionType):
        return from_dict(next(t for t in args if t is not type(None)), value)
    if origin is list:
        return [from_dict(args[0], item) for item in value]
    if origin is dict:
        return {key: from_dict(args[1], item) for key, item in value.items()}
    if model is datetime:
        return datetime.fromisoformat(value)
    if isinstance(model, type) and issubclass(model, Enum):
        return model(value)
    if is_dataclass(model):
        hints = get_type_hints(model)
        return model(**{f.name: from_dict(hints[f.name], value[f.name])
                        for f in fields(model) if f.name in value})
    return value
