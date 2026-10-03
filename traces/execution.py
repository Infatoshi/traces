"""Normalize redacted conversations into causal execution messages.

The extractor layer intentionally keeps a small compatibility vocabulary
(``tool_use``/``tool_calls`` and ``tool_result``/``tool_results``).  This
module is the single boundary that turns those aliases into one public,
source-independent execution schema.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

TOOL_CALL_FIELDS = ("tool_call", "tool_calls", "tool_use", "tool_uses")
TOOL_RESULT_FIELDS = ("tool_result", "tool_results")
DIFF_FIELDS = (
    "diffs",
    "edits",
    "suggested_diffs",
    "suggested_code_blocks",
    "diff_histories",
)


def _items(message: dict[str, Any], fields: Iterable[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for field in fields:
        value = message.get(field)
        if isinstance(value, dict):
            items.append(value)
        elif isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


def _tool_call(item: dict[str, Any]) -> dict[str, Any]:
    function = item.get("function") if isinstance(item.get("function"), dict) else {}
    arguments: Any
    if "input" in item:
        arguments = item["input"]
    elif "arguments" in item:
        arguments = item["arguments"]
    elif "arguments" in function:
        arguments = function["arguments"]
    else:
        arguments = {}
    call = {
        "id": item.get("id") or item.get("call_id"),
        "name": item.get("name") or item.get("tool") or function.get("name"),
        "arguments": arguments,
    }
    kind = item.get("kind")
    if isinstance(kind, str) and kind:
        call["kind"] = kind
    return call


def _tool_result(
    item: dict[str, Any], fallback_content: Any = ""
) -> dict[str, Any]:
    if "output" in item:
        content = item["output"]
    elif "content" in item:
        content = item["content"]
    elif "text" in item:
        content = item["text"]
    else:
        content = fallback_content
    result = {
        "role": "tool",
        "tool_call_id": item.get("tool_call_id") or item.get("call_id") or item.get("id"),
        "name": item.get("name") or item.get("tool"),
        "content": content,
    }
    if item.get("status") is not None:
        result["status"] = item["status"]
    for field in ("details", "metadata"):
        if item.get(field) is not None:
            result[field] = item[field]
    return result


def _artifacts(message: dict[str, Any]) -> list[Any]:
    artifacts: list[Any] = []
    for field in DIFF_FIELDS:
        value = message.get(field)
        if isinstance(value, list):
            artifacts.extend(value)
        elif value is not None:
            artifacts.append(value)
    return artifacts

def _assistant_metadata(
    message: dict[str, Any], *, include_reasoning: bool
) -> dict[str, Any]:
    keys = [
        "model",
        "reasoning_effort",
        "tool_schema_hash",
        "finish_reason",
    ]
    if include_reasoning:
        keys.append("reasoning")
    return {
        key: message[key]
        for key in keys
        if message.get(key) not in (None, "")
    }



def normalize_execution_messages(
    conversation: dict[str, Any], *, include_reasoning: bool = False
) -> list[dict[str, Any]]:
    """Return ordered public execution messages for one conversation.

    Tool results attached to provider snapshot messages are split out into
    explicit ``role=tool`` messages.  If the same snapshot also contains final
    assistant text, that text is emitted after the tool results so downstream
    staircase examples have the correct causal boundary.
    """
    normalized: list[dict[str, Any]] = []
    call_names: dict[str, Any] = {}
    for message in conversation.get("messages", []):
        if not isinstance(message, dict):
            continue
        if message.get("phase") == "reasoning" and not include_reasoning:
            continue

        role = str(message.get("role") or "user")
        content = message.get("content", "")
        calls = [_tool_call(item) for item in _items(message, TOOL_CALL_FIELDS)]
        for call in calls:
            if isinstance(call.get("id"), str) and call.get("name"):
                call_names[call["id"]] = call["name"]
        results = [
            _tool_result(item, content if role == "tool" else "")
            for item in _items(message, TOOL_RESULT_FIELDS)
        ]
        for result in results:
            result_id = result.get("tool_call_id")
            if not result.get("name") and isinstance(result_id, str):
                result["name"] = call_names.get(result_id)
        artifacts = _artifacts(message)
        if artifacts and results:
            results[-1]["artifacts"] = artifacts

        if calls:
            call_message: dict[str, Any] = {
                "role": "assistant",
                "content": "" if results and content else content,
                "tool_calls": calls,
                **_assistant_metadata(message, include_reasoning=include_reasoning),
            }
            if artifacts and not results:
                call_message["artifacts"] = artifacts
            normalized.append(call_message)
        elif role not in {"tool", "assistant"}:
            base: dict[str, Any] = {"role": role, "content": content}
            for key in ("phase", "origin_kind"):
                if message.get(key) not in (None, ""):
                    base[key] = message[key]
            if artifacts:
                base["artifacts"] = artifacts
            normalized.append(base)
        elif role == "assistant" and not results and content:
            base = {"role": "assistant", "content": content}
            base.update(_assistant_metadata(message, include_reasoning=include_reasoning))
            if artifacts:
                base["artifacts"] = artifacts
            normalized.append(base)
        elif role == "tool" and not results:
            normalized.append({"role": "tool", "content": content})

        normalized.extend(results)

        if role == "assistant" and results and content:
            normalized.append({"role": "assistant", "content": content})

    return normalized


def split_execution_tasks(conversation: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Split a conversation at real user turns while retaining system context."""
    messages = normalize_execution_messages(conversation)
    persistent: list[dict[str, Any]] = []
    prefix: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    tasks: list[list[dict[str, Any]]] = []

    def finish() -> None:
        if current:
            tasks.append([*prefix, *current])
            current.clear()

    for message in messages:
        role = message.get("role")
        if role in {"system", "developer"}:
            if current:
                current.append(message)
            persistent.append(message)
        elif role == "user" and message.get("phase") != "injection":
            finish()
            prefix = list(persistent)
            current.append(message)
        else:
            if not current:
                prefix = list(persistent)
            current.append(message)
    finish()
    return tasks


def execution_summary_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute structural completeness without treating it as task success."""
    call_ids: list[str] = []
    missing_call_ids = 0
    result_ids: list[str] = []
    missing_result_ids = 0
    assistant_steps = 0

    for message in messages:
        if message.get("role") == "assistant" and (
            message.get("content") or message.get("tool_calls")
        ):
            assistant_steps += 1
        for call in message.get("tool_calls", []):
            call_id = call.get("id")
            if isinstance(call_id, str) and call_id:
                call_ids.append(call_id)
            else:
                missing_call_ids += 1
        if message.get("role") == "tool":
            result_id = message.get("tool_call_id")
            if isinstance(result_id, str) and result_id:
                result_ids.append(result_id)
            else:
                missing_result_ids += 1

    call_counts = Counter(call_ids)
    result_counts = Counter(result_ids)
    matched = sum(min(count, result_counts[call_id]) for call_id, count in call_counts.items())
    dangling = sum((call_counts - result_counts).values()) + missing_call_ids
    orphan = sum((result_counts - call_counts).values()) + missing_result_ids
    final_assistant = bool(
        messages
        and messages[-1].get("role") == "assistant"
        and bool(messages[-1].get("content"))
    )
    return {
        "tool_calls": len(call_ids) + missing_call_ids,
        "tool_results": len(result_ids) + missing_result_ids,
        "matched_tool_calls": matched,
        "dangling_tool_calls": dangling,
        "orphan_tool_results": orphan,
        "tools_complete": bool(call_ids or missing_call_ids)
        and dangling == 0
        and orphan == 0,
        "final_assistant": final_assistant,
        "assistant_steps": assistant_steps,
    }


def execution_summary(conversation: dict[str, Any]) -> dict[str, Any]:
    """Compute whole-conversation structural completeness for filters/stats."""
    return execution_summary_messages(normalize_execution_messages(conversation))
