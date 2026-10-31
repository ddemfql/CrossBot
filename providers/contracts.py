"""Small transport boundary; real API configuration is implemented separately."""
from typing import Protocol


class ChatClient(Protocol):
    """Accept API-style role/content messages, return generated text or raise."""

    def complete(self, messages: list[dict[str, str]]) -> str: ...


def client_metadata(client):
    return {key: value if isinstance(value := getattr(client, key, None), str) else None
            for key in ("provider", "model")}
