"""Conversation-level filters applied before export."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from fnmatch import fnmatchcase

from traces.config import ExportConfig
from traces.execution import execution_summary
from traces.export.metadata import build_metadata


def _matches(value: object, patterns: list[str]) -> bool:
    if not isinstance(value, (str, int)):
        return False
    normalized = str(value).casefold()
    return any(fnmatchcase(normalized, pattern.casefold()) for pattern in patterns)


def _timestamp(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 1000 if value > 10_000_000_000 else float(value)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def filter_reason(conv: dict, config: ExportConfig) -> str | None:
    """Return the first exclusion reason for a normalized conversation."""
    metadata = build_metadata(conv, metadata_key=config.metadata_key)
    counts = metadata["counts"]
    source = metadata["source"]

    if config.include_sources and not _matches(source, config.include_sources):
        return "source_not_included"
    if config.drop_sources and _matches(source, config.drop_sources):
        return "source_dropped"

    project_values = [
        conv.get(key)
        for key in ("project_id", "project_hash", "project_path", "workspace_id")
    ]
    if config.projects and not any(
        _matches(value, config.projects) for value in project_values
    ):
        return "project_not_included"
    if config.models and not any(
        _matches(model, config.models) for model in metadata["models"]
    ):
        return "model_not_included"
    if config.statuses and not _matches(conv.get("status") or "unknown", config.statuses):
        return "status_not_included"
    if config.reasoning_efforts and not _matches(
        conv.get("reasoning_effort") or "unknown", config.reasoning_efforts
    ):
        return "reasoning_effort_not_included"
    if config.session_kinds and not _matches(
        conv.get("session_kind") or "unknown", config.session_kinds
    ):
        return "session_kind_not_included"

    created_at = _timestamp(conv.get("created_at"))
    if config.since:
        since = _timestamp(config.since)
        if since is None:
            raise ValueError(f"invalid --since timestamp: {config.since!r}")
        if created_at is None or created_at < since:
            return "before_since"
    if config.until:
        until = _timestamp(config.until)
        if until is None:
            raise ValueError(f"invalid --until timestamp: {config.until!r}")
        if created_at is None or created_at > until:
            return "after_until"

    if config.min_turns is not None and counts["messages"] < config.min_turns:
        return "below_min_messages"
    if config.max_turns is not None and counts["messages"] > config.max_turns:
        return "above_max_messages"
    if (
        config.min_assistant_chars is not None
        and counts["assistant_chars"] < config.min_assistant_chars
    ):
        return "below_min_assistant_chars"
    if config.require_tools and counts["tool_calls"] == 0:
        return "tools_required"
    if config.require_diffs and counts["diffs"] == 0:
        return "diffs_required"
    if config.require_complete_tools or config.require_final_assistant:
        execution = execution_summary(conv)
        if config.require_complete_tools and not execution["tools_complete"]:
            return "complete_tools_required"
        if config.require_final_assistant and not execution["final_assistant"]:
            return "final_assistant_required"
    return None


def passes(conv: dict, config: ExportConfig) -> bool:
    return filter_reason(conv, config) is None


def dedup_key(conv: dict) -> str:
    """Stable source-namespaced identity for exact deduplication."""
    source = str(conv.get("source") or "unknown")
    if conv.get("session_id"):
        return f"sid:{source}:{conv['session_id']}"
    blob = json.dumps(
        conv.get("messages", []), sort_keys=True, separators=(",", ":")
    ).encode()
    return "h:" + hashlib.sha256(blob).hexdigest()
