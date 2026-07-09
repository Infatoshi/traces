"""Conversation-level filters applied before export."""
from __future__ import annotations

from traces.config import ExportConfig


def passes(conv: dict, config: ExportConfig) -> bool:
    msgs = conv.get("messages", [])
    if config.min_turns is not None and len(msgs) < config.min_turns:
        return False
    if config.max_turns is not None and len(msgs) > config.max_turns:
        return False
    if config.min_assistant_chars is not None:
        asst_chars = sum(len(m.get("content", "")) for m in msgs if m.get("role") == "assistant")
        if asst_chars < config.min_assistant_chars:
            return False
    if config.drop_sources and conv.get("source") in config.drop_sources:
        return False
    return True


def dedup_key(conv: dict) -> str:
    """Stable identity for dedup. Uses session_id when present, else content hash."""
    import hashlib
    if conv.get("session_id"):
        return f"sid:{conv['session_id']}"
    blob = repr(conv.get("messages", [])).encode()
    return "h:" + hashlib.sha256(blob).hexdigest()
