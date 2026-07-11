"""Metadata derived from the normalized traces conversation schema.

Provider-specific parsing belongs in ``traces.extract``. This module only
reads fields declared by ``Conversation`` and ``Message``; it never searches
message text or guesses provider field aliases.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections import Counter
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "1.0"
_PROCESS_KEY = (
    os.environ.get("TRACES_METADATA_KEY") or secrets.token_hex(32)
).encode()

# Compatibility names in the normalized Message type. These are not
# provider field names and are never applied to raw source records.
_TOOL_CALL_FIELDS = ("tool_call", "tool_calls", "tool_use", "tool_uses")
_TOOL_RESULT_FIELDS = ("tool_result", "tool_results")
_DIFF_FIELDS = (
    "diffs",
    "edits",
    "suggested_diffs",
    "suggested_code_blocks",
    "diff_histories",
)
_TOKEN_FIELDS = {
    "input",
    "output",
    "cached_input",
    "cache_write",
    "reasoning",
    "tool",
    "total",
}


def _key_bytes(metadata_key: str | bytes | None) -> bytes:
    if isinstance(metadata_key, bytes):
        return metadata_key
    if isinstance(metadata_key, str):
        return metadata_key.encode()
    return _PROCESS_KEY


def _opaque_id(kind: str, value: object, metadata_key: str | bytes | None) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    digest = hmac.new(_key_bytes(metadata_key), encoded, hashlib.sha256).hexdigest()
    prefix = "ts" if kind == "trace" else "tp"
    return f"{prefix}_{digest[:24]}"


def trace_id(conv: dict[str, Any], metadata_key: str | bytes | None = None) -> str:
    """Return a source-namespaced opaque identity for a normalized trace."""
    source = str(conv.get("source") or "unknown")
    session_id = conv.get("session_id")
    if isinstance(session_id, (str, int)) and session_id != "":
        identity: object = [source, session_id]
    else:
        identity = {
            "source": source,
            "messages": [
                message
                for message in conv.get("messages", [])
                if isinstance(message, dict)
            ],
            "project": {
                key: conv.get(key)
                for key in ("project_id", "project_hash", "project_path", "workspace_id")
                if conv.get(key) is not None
            },
            "created_at": conv.get("created_at"),
        }
    return _opaque_id("trace", identity, metadata_key)


def _day(value: object) -> str | None:
    """Return a UTC day for a normalized timestamp, without format guessing."""
    parsed: datetime
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value) / 1000 if value > 10_000_000_000 else float(value)
        try:
            parsed = datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _count(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    return int(value is not None and value != "")


def _message_count(messages: list[dict[str, Any]], fields: tuple[str, ...]) -> int:
    return sum(_count(message.get(field)) for message in messages for field in fields)


def _models(conv: dict[str, Any], messages: list[dict[str, Any]]) -> list[str]:
    ordered: list[str] = []
    for value in [conv.get("model"), *(message.get("model") for message in messages)]:
        if isinstance(value, str) and value and value not in ordered:
            ordered.append(value)
    return ordered


def _token_usage(conv: dict[str, Any]) -> dict[str, int | float]:
    value = conv.get("token_usage")
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in _TOKEN_FIELDS
        and isinstance(item, (int, float))
        and not isinstance(item, bool)
    }


def build_metadata(
    conv: dict[str, Any], metadata_key: str | bytes | None = None
) -> dict[str, Any]:
    """Build public metadata from one normalized, redacted conversation."""
    messages = [
        message for message in conv.get("messages", []) if isinstance(message, dict)
    ]
    roles = Counter(str(message.get("role") or "unknown") for message in messages)
    tool_calls = _message_count(messages, _TOOL_CALL_FIELDS) + int(
        conv.get("tool_call_count") or 0
    )
    tool_results = _message_count(messages, _TOOL_RESULT_FIELDS) + int(
        conv.get("tool_result_count") or 0
    )
    diffs = _message_count(messages, _DIFF_FIELDS) + int(conv.get("diff_count") or 0)

    session = {
        key: value
        for key, value in {
            "created_at": _day(conv.get("created_at")),
            "updated_at": _day(conv.get("updated_at")),
            "status": conv.get("status"),
            "version": conv.get("version"),
            "reasoning_effort": conv.get("reasoning_effort"),
            "kind": conv.get("session_kind"),
            "sandbox_profile": conv.get("sandbox_profile"),
        }.items()
        if isinstance(value, (str, int, float, bool)) and value != ""
    }

    project_identity = next(
        (
            conv.get(key)
            for key in ("project_id", "project_hash", "project_path", "workspace_id")
            if isinstance(conv.get(key), (str, int)) and conv.get(key) != ""
        ),
        None,
    )
    project = (
        {"id": _opaque_id("project", project_identity, metadata_key)}
        if project_identity is not None
        else {}
    )

    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id(conv, metadata_key),
        "source": str(conv.get("source") or "unknown"),
        "session": session,
        "project": project,
        "models": _models(conv, messages),
        "counts": {
            "messages": len(messages),
            "roles": dict(roles),
            "assistant_chars": sum(
                len(str(message.get("content") or ""))
                for message in messages
                if message.get("role") == "assistant"
            ),
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "diffs": diffs,
        },
    }
    token_usage = _token_usage(conv)
    if token_usage:
        metadata["token_usage"] = token_usage
    return metadata
