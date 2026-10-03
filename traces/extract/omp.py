"""Oh My Pi session extractor preserving native messages and tool outcomes."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from traces.extract.base import Conversation


def find_omp_installations(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    install = home / ".omp"
    return [install] if (install / "agent" / "sessions").is_dir() else []


def _content(parts: object, field: str = "text") -> str:
    if not isinstance(parts, list):
        return ""
    values = [part.get(field) for part in parts if isinstance(part, dict)]
    return "\n".join(value for value in values if isinstance(value, str) and value)


def _numeric_usage(target: dict[str, int | float], raw: object) -> None:
    if not isinstance(raw, dict):
        return
    mapping = {
        "input": "input",
        "output": "output",
        "cacheRead": "cached_input",
        "cacheWrite": "cache_write",
    }
    for source_key, target_key in mapping.items():
        value = raw.get(source_key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            target[target_key] = target.get(target_key, 0) + value


def extract_omp_session(session_file: Path) -> Conversation | None:
    messages: list[dict[str, Any]] = []
    session: dict[str, Any] = {}
    title: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    status: str | None = None
    updated_at: object = None
    tool_call_count = 0
    tool_result_count = 0
    token_usage: dict[str, int | float] = {}

    try:
        stream = session_file.open()
    except OSError:
        return None
    with stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            record_type = record.get("type")
            timestamp = record.get("timestamp")
            if timestamp is not None:
                updated_at = timestamp

            if record_type == "session":
                session = record
                candidate = record.get("model")
                if isinstance(candidate, str):
                    model = candidate
                continue
            if record_type in {"title", "title_change"}:
                candidate = record.get("title")
                if isinstance(candidate, str):
                    title = candidate
                continue
            if record_type == "model_change":
                candidate = record.get("model")
                if isinstance(candidate, str):
                    model = candidate
                continue
            if record_type == "thinking_level_change":
                candidate = record.get("thinkingLevel")
                if isinstance(candidate, str):
                    reasoning_effort = candidate
                continue
            if record_type == "custom" and record.get("customType") == "session_exit":
                data = record.get("data")
                if isinstance(data, dict):
                    reason = data.get("reason")
                    status = reason if isinstance(reason, str) else "exited"
                continue
            if record_type == "custom_message":
                content = record.get("content")
                if isinstance(content, str) and content:
                    messages.append({
                        "id": record.get("id"),
                        "parent_id": record.get("parentId"),
                        "role": "user",
                        "content": content,
                        "timestamp": timestamp,
                        "phase": "injection",
                        "origin_kind": record.get("customType"),
                    })
                continue
            if record_type != "message":
                continue

            raw = record.get("message")
            if not isinstance(raw, dict):
                continue
            role = raw.get("role")
            if role == "toolResult":
                output = _content(raw.get("content"))
                is_error = bool(raw.get("isError"))
                details = raw.get("details")
                result: dict[str, Any] = {
                    "tool_call_id": raw.get("toolCallId"),
                    "tool": raw.get("toolName"),
                    "output": output,
                    "status": "error" if is_error else "success",
                    "is_error": is_error,
                }
                if isinstance(details, dict):
                    result["details"] = details
                messages.append({
                    "id": record.get("id"),
                    "parent_id": record.get("parentId"),
                    "role": "tool",
                    "content": output,
                    "timestamp": timestamp,
                    "tool_result": result,
                })
                tool_result_count += 1
                continue
            if role not in {"system", "user", "assistant"}:
                continue

            parts = raw.get("content")
            message: dict[str, Any] = {
                "id": record.get("id"),
                "parent_id": record.get("parentId"),
                "role": role,
                "content": _content(parts),
                "timestamp": timestamp,
            }
            message_model = raw.get("model")
            if isinstance(message_model, str):
                model = message_model
                message["model"] = message_model
            if role == "assistant":
                reasoning = _content(parts, "thinking")
                if reasoning:
                    message["reasoning"] = reasoning
                calls: list[dict[str, Any]] = []
                if isinstance(parts, list):
                    for part in parts:
                        if not isinstance(part, dict) or part.get("type") != "toolCall":
                            continue
                        calls.append({
                            "id": part.get("id"),
                            "name": part.get("name"),
                            "input": (
                                part.get("arguments")
                                if isinstance(part.get("arguments"), dict)
                                else {}
                            ),
                            "kind": "function",
                        })
                if calls:
                    message["tool_calls"] = calls
                    tool_call_count += len(calls)
                finish_reason = raw.get("stopReason")
                if isinstance(finish_reason, str):
                    message["finish_reason"] = finish_reason
                _numeric_usage(token_usage, raw.get("usage"))
            messages.append(message)

    if not messages:
        return None
    token_usage["total"] = sum(token_usage.values())
    session_id = session.get("id")
    if not isinstance(session_id, str):
        session_id = session_file.stem
    project_path = session.get("cwd")
    return {
        "source": "omp",
        "session_id": session_id,
        "name": title or session.get("title"),
        "project_path": project_path,
        "created_at": session.get("timestamp"),
        "updated_at": updated_at,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "status": status,
        "messages": messages,
        "tool_schema_quality": "missing_not_recorded",
        "token_usage": token_usage,
        "tool_call_count": 0,
        "tool_result_count": 0,
        "native_tool_call_count": tool_call_count,
        "native_tool_result_count": tool_result_count,
        "source_file": str(session_file),
    }


class OmpExtractor:
    source = "omp"

    def __init__(self, home: Path | None = None) -> None:
        self.home = home

    def find_installations(self) -> list[Path]:
        return find_omp_installations(self.home)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        sessions = install_dir / "agent" / "sessions"
        for path in sorted(sessions.glob("**/*.jsonl")):
            conversation = extract_omp_session(path)
            if conversation is not None:
                yield conversation
