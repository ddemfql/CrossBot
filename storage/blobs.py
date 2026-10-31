"""Content-addressed originals. Names are metadata, never filesystem paths."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import re
import tempfile
from core.errors import CrossBotError, ErrorCode, ValidationError


class LocalBlobStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes) -> str:
        if not isinstance(content, bytes):
            raise ValidationError("文件内容必须为 bytes")
        key = hashlib.sha256(content).hexdigest()
        with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as temp:
            temporary = Path(temp.name)
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
        try:
            try:
                os.link(temporary, self.root / key)
            except FileExistsError:
                if self.get(key) != content:
                    raise CrossBotError(ErrorCode.CONFLICT, "文件哈希与已保存内容不一致")
        finally:
            temporary.unlink(missing_ok=True)
        return key

    def get(self, key: str) -> bytes:
        if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise ValidationError("文件标识无效")
        target = self.root / key
        if target.is_symlink():
            raise ValidationError("文件路径无效")
        try:
            descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as stream:
                content = stream.read()
        except FileNotFoundError:
            raise CrossBotError(ErrorCode.NOT_FOUND, "文件不存在") from None
        except OSError:
            raise CrossBotError(ErrorCode.CONFLICT, "无法读取原始文件") from None
        if hashlib.sha256(content).hexdigest() != key:
            raise CrossBotError(ErrorCode.CONFLICT, "文件完整性检查失败")
        return content

