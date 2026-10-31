"""Reload the stateless generation UI when a long-running server has stale code."""
from hashlib import sha256
from importlib import import_module, reload
from pathlib import Path


def load_revision_panel():
    client = import_module("local_lab.deepseek_client")
    client_digest = sha256(Path(client.__file__).read_bytes()).hexdigest()
    client_changed = getattr(client, "SOURCE_SHA256", None) != client_digest
    if client_changed:
        reload(client)
    revisions = import_module("local_lab.revisions")
    revision_digest = sha256(Path(revisions.__file__).read_bytes()).hexdigest()
    dependency_changed = getattr(revisions, "SOURCE_SHA256", None) != revision_digest
    if dependency_changed:
        reload(revisions)
    dialogue = import_module("local_lab.dialogue")
    dialogue_changed = getattr(dialogue, "SOURCE_SHA256", None) != sha256(Path(dialogue.__file__).read_bytes()).hexdigest()
    if dialogue_changed:
        reload(dialogue)
        dependency_changed = True
    for name in ("dialogue_panel", "payment_wording_panel", "rfq_panel", "history_recovery"):
        dependency = import_module("local_lab." + name)
        digest = sha256(Path(dependency.__file__).read_bytes()).hexdigest()
        if (name == "dialogue_panel" and dialogue_changed) or getattr(dependency, "SOURCE_SHA256", None) != digest:
            reload(dependency)
            dependency_changed = True
    module = import_module("local_lab.revision_panel")
    digest = sha256(Path(module.__file__).read_bytes()).hexdigest()
    if client_changed or dependency_changed or getattr(module, "SOURCE_SHA256", None) != digest:
        module = reload(module)
    return module
