"""Environment-backed configuration shared by all application layers."""
from __future__ import annotations
import os
from dataclasses import dataclass

def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

@dataclass(frozen=True, slots=True)
class AppConfig:
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    model_name: str = "gpt-3.5-turbo"
    mock_mode: bool = False
    request_timeout_seconds: float = 60.0
    max_upload_size_mb: int = 20
    storage_dir: str = "data"
    model_api_protocol: str = "auto"

    @property
    def has_provider_credentials(self):
        return bool(self.openai_api_key and self.openai_api_key.strip())

def load_config(environ: dict[str, str] | None = None) -> AppConfig:
    values = os.environ if environ is None else environ
    api_key = values.get("OPENAI_API_KEY") or None
    return AppConfig(
        openai_api_key=api_key,
        openai_base_url=values.get("OPENAI_BASE_URL") or None,
        model_name=values.get("MODEL_NAME", "gpt-3.5-turbo"),
        mock_mode=_as_bool(values.get("MOCK_MODE"), default=not bool(api_key)),
        request_timeout_seconds=float(values.get("REQUEST_TIMEOUT_SECONDS", "60")),
        max_upload_size_mb=int(values.get("MAX_UPLOAD_SIZE_MB", "20")),
        storage_dir=values.get("STORAGE_DIR", "data"),
        model_api_protocol=values.get("MODEL_API_PROTOCOL", "auto"),
    )
