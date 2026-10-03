"""Kimi Code extractor preserving native reasoning, calls, results, and tool schemas."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from traces.extract.base import Conversation


def find_kimi_code_installations(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    install = home / ".kimi-code"
    return [install] if (install / "sessions").is_dir() else []


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _text_parts(parts: object, key: str = "text") -> str:
    if not isinstance(parts, list):
        return ""
    values = [part.get(key) for part in parts if isinstance(part, dict)]
    return "\n".join(value for value in values if isinstance(value, str) and value)


def _tool_schema(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    tools = snapshot.get("tools")
    if not isinstance(tools, list):
        return []
    result: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            continue
        function = {
            key: tool[key]
            for key in ("name", "description", "parameters")
            if key in tool
        }
        result.append({"type": "function", "function": function})
    return result


def extract_kimi_agent(agent_dir: Path) -> Conversation | None:
    wire = agent_dir / "wire.jsonl"
    if not wire.is_file():
        return None

    session_dir = agent_dir.parent.parent
    state = _load_json(session_dir / "state.json")
    agent_id = agent_dir.name
    agent_state = state.get("agents", {}).get(agent_id, {})
    if not isinstance(agent_state, dict):
        agent_state = {}

    messages: list[dict[str, Any]] = []
    assistant_by_step: dict[str, dict[str, Any]] = {}
    tool_snapshots: list[dict[str, Any]] = []
    seen_snapshot_hashes: set[str] = set()
    tool_names: dict[str, str] = {}
    token_usage = {
        "input": 0,
        "output": 0,
        "cached_input": 0,
        "cache_write": 0,
    }
    model: str | None = None
    reasoning_effort: str | None = None
    current_schema_hash: str | None = None
    updated_at: object = state.get("updatedAt")
    tool_call_count = 0
    tool_result_count = 0

    def assistant_message(event: dict[str, Any], timestamp: object) -> dict[str, Any]:
        nonlocal model, reasoning_effort, current_schema_hash
        step_key = str(event.get("stepUuid") or event.get("uuid") or event.get("turnId") or len(messages))
        existing = assistant_by_step.get(step_key)
        if existing is not None:
            return existing
        message: dict[str, Any] = {
            "id": event.get("stepUuid") or event.get("uuid"),
            "role": "assistant",
            "content": "",
            "timestamp": timestamp,
        }
        if model:
            message["model"] = model
        if reasoning_effort:
            message["reasoning_effort"] = reasoning_effort
        if current_schema_hash:
            message["tool_schema_hash"] = current_schema_hash
        messages.append(message)
        assistant_by_step[step_key] = message
        return message

    try:
        stream = wire.open()
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
            timestamp = record.get("time")
            if timestamp is not None:
                updated_at = timestamp

            if record_type == "llm.tools_snapshot":
                schema_hash = record.get("hash")
                tools = _tool_schema(record)
                if not isinstance(schema_hash, str) or schema_hash in seen_snapshot_hashes:
                    continue
                tool_snapshots.append({
                    "hash": schema_hash,
                    "tools": tools,
                    "timestamp": timestamp,
                    "quality": "captured_exact",
                })
                seen_snapshot_hashes.add(schema_hash)
                continue

            if record_type == "llm.request":
                request_model = record.get("model")
                if isinstance(request_model, str):
                    model = request_model
                effort = record.get("thinkingEffort")
                if isinstance(effort, str):
                    reasoning_effort = effort
                schema_hash = record.get("toolsHash")
                current_schema_hash = schema_hash if isinstance(schema_hash, str) else None
                continue

            if record_type == "context.append_message":
                raw = record.get("message")
                if not isinstance(raw, dict):
                    continue
                role = raw.get("role")
                if role not in {"system", "user", "assistant", "tool"}:
                    continue
                origin = raw.get("origin")
                origin_kind = origin.get("kind") if isinstance(origin, dict) else None
                content = _text_parts(raw.get("content"))
                if content or role != "assistant":
                    message = {
                        "role": role,
                        "content": content,
                        "timestamp": timestamp,
                    }
                    if role == "user" and isinstance(origin_kind, str) and origin_kind != "user":
                        message["phase"] = "injection"
                        message["origin_kind"] = origin_kind
                    messages.append(message)
                continue

            if record_type != "context.append_loop_event":
                continue
            event = record.get("event")
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")

            if event_type == "content.part":
                part = event.get("part")
                if not isinstance(part, dict):
                    continue
                message = assistant_message(event, timestamp)
                text = part.get("text")
                if isinstance(text, str) and text:
                    message["content"] = str(message.get("content") or "") + text
                thinking = part.get("think")
                if isinstance(thinking, str) and thinking:
                    message["reasoning"] = str(message.get("reasoning") or "") + thinking
                continue

            if event_type == "tool.call":
                message = assistant_message(event, timestamp)
                call = {
                    "id": event.get("toolCallId"),
                    "name": event.get("name"),
                    "input": event.get("args") if isinstance(event.get("args"), dict) else {},
                    "kind": "function",
                }
                message.setdefault("tool_calls", []).append(call)
                call_id = call.get("id")
                if isinstance(call_id, str) and isinstance(call.get("name"), str):
                    tool_names[call_id] = call["name"]
                tool_call_count += 1
                continue

            if event_type == "tool.result":
                call_id = event.get("toolCallId")
                raw_result = event.get("result")
                if not isinstance(raw_result, dict):
                    raw_result = {"output": raw_result}
                result = {
                    "tool_call_id": call_id,
                    "tool": tool_names.get(str(call_id)),
                    "output": raw_result.get("output"),
                    "status": "error" if raw_result.get("isError") else "success",
                    "is_error": bool(raw_result.get("isError")),
                }
                if "note" in raw_result:
                    result["note"] = raw_result["note"]
                messages.append({
                    "role": "tool",
                    "content": str(raw_result.get("output") or ""),
                    "timestamp": timestamp,
                    "tool_result": result,
                })
                tool_result_count += 1
                continue

            if event_type == "step.end":
                usage = event.get("usage")
                if isinstance(usage, dict):
                    mappings = {
                        "inputOther": "input",
                        "output": "output",
                        "inputCacheRead": "cached_input",
                        "inputCacheCreation": "cache_write",
                    }
                    for source_key, target_key in mappings.items():
                        value = usage.get(source_key)
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            token_usage[target_key] += value
                message_id = event.get("messageId")
                step_key = event.get("stepUuid") or event.get("uuid")
                candidate = assistant_by_step.get(str(step_key))
                if isinstance(message_id, str) and candidate is not None:
                    candidate["id"] = message_id
                    finish_reason = event.get("finishReason")
                    if isinstance(finish_reason, str):
                        candidate["finish_reason"] = finish_reason

    if not messages:
        return None
    token_usage["total"] = sum(token_usage.values())
    session_id = session_dir.name
    parent_agent_id = agent_state.get("parentAgentId")
    return {
        "source": "kimi_code",
        "session_id": f"{session_id}:{agent_id}",
        "parent_session_id": (
            f"{session_id}:{parent_agent_id}" if isinstance(parent_agent_id, str) else None
        ),
        "session_kind": agent_state.get("type") or ("main" if agent_id == "main" else "subagent"),
        "name": state.get("title"),
        "project_path": state.get("workDir"),
        "created_at": state.get("createdAt"),
        "updated_at": updated_at,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "messages": messages,
        "tool_snapshots": tool_snapshots,
        "tool_schema_hash": current_schema_hash,
        "tool_schema_quality": "captured_exact" if tool_snapshots else "missing_not_recorded",
        "token_usage": token_usage,
        "tool_call_count": 0,
        "tool_result_count": 0,
        "native_tool_call_count": tool_call_count,
        "native_tool_result_count": tool_result_count,
        "source_file": str(wire),
    }


class KimiCodeExtractor:
    source = "kimi_code"

    def __init__(self, home: Path | None = None) -> None:
        self.home = home

    def find_installations(self) -> list[Path]:
        return find_kimi_code_installations(self.home)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        sessions = install_dir / "sessions"
        for wire in sorted(sessions.glob("**/agents/*/wire.jsonl")):
            conversation = extract_kimi_agent(wire.parent)
            if conversation is not None:
                yield conversation
