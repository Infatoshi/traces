"""Native execution and assistant-step exporters."""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from traces.config import ExportConfig
from traces.execution import execution_summary_messages, split_execution_tasks
from traces.export.filters import dedup_key, filter_reason
from traces.export.metadata import build_metadata

SCHEMA_VERSION = "execution.v1"
STEP_SCHEMA_VERSION = "assistant-steps.v1"


def _iter_conversations(in_dir: Path) -> Iterator[dict[str, Any]]:
    for source in sorted(in_dir.glob("*.jsonl")):
        with source.open() as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)


def _selected(
    in_dir: Path, config: ExportConfig
) -> Iterator[tuple[dict[str, Any], list[list[dict[str, Any]]]]]:
    seen: set[str] = set()
    conversation_config = replace(
        config,
        require_complete_tools=False,
        require_final_assistant=False,
    )
    for conversation in _iter_conversations(in_dir):
        if filter_reason(conversation, conversation_config):
            continue
        if config.dedup:
            key = dedup_key(conversation)
            if key in seen:
                continue
            seen.add(key)
        yield conversation, split_execution_tasks(conversation)


def _task_selected(summary: dict[str, Any], config: ExportConfig) -> bool:
    if config.require_complete_tools and not summary["tools_complete"]:
        return False
    if config.require_final_assistant and not summary["final_assistant"]:
        return False
    return True


def _assistant_steps(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "assistant"
        and (message.get("content") or message.get("tool_calls"))
    ]
    return [
        {
            "index": step_index,
            "message_index": message_index,
            "kind": (
                "tool_call" if messages[message_index].get("tool_calls") else "text"
            ),
            **(
                {"tool_schema_hash": messages[message_index]["tool_schema_hash"]}
                if messages[message_index].get("tool_schema_hash")
                else {}
            ),
        }
        for step_index, message_index in enumerate(targets)
    ]


def _execution_id(metadata: dict[str, Any], task_index: int) -> str:
    return f"{metadata['trace_id']}:task:{task_index}"


def _tool_context(conversation: dict[str, Any]) -> dict[str, Any]:
    return {
        key: conversation[key]
        for key in (
            "tools",
            "tool_snapshots",
            "tool_schema_hash",
            "tool_schema_quality",
        )
        if conversation.get(key)
    }


def export_execution(in_dir: Path, out_file: Path, config: ExportConfig) -> dict:
    """Write one causally ordered native execution row per user task."""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    conversations = 0
    dropped_no_assistant = 0
    rows_by_source: Counter[str] = Counter()
    with out_file.open("w") as stream:
        for conversation, tasks in _selected(in_dir, config):
            metadata = build_metadata(
                conversation, metadata_key=config.metadata_key
            )
            wrote_conversation = False
            for task_index, messages in enumerate(tasks):
                summary = execution_summary_messages(messages)
                if summary["assistant_steps"] == 0:
                    dropped_no_assistant += 1
                    continue
                if not _task_selected(summary, config):
                    continue
                steps = _assistant_steps(messages)
                stream.write(json.dumps({
                    "schema_version": SCHEMA_VERSION,
                    "execution_id": _execution_id(metadata, task_index),
                    "messages": messages,
                    "assistant_steps": steps,
                    "task": {"index": task_index, "count": len(tasks)},
                    "execution": summary,
                    **_tool_context(conversation),
                    "metadata": metadata,
                }, ensure_ascii=False) + "\n")
                rows += 1
                wrote_conversation = True
                rows_by_source[metadata["source"]] += 1
            conversations += int(wrote_conversation)
    return {
        "rows": rows,
        "conversations": conversations,
        "dropped_no_assistant": dropped_no_assistant,
        "by_source": dict(rows_by_source),
    }


def export_assistant_steps(
    in_dir: Path, out_file: Path, config: ExportConfig
) -> dict:
    """Write one task row with explicit assistant target indices.

    Each row references an ``execution.jsonl`` row by ``execution_id``. A
    loader materializes step ``i`` as ``messages[:message_index]`` context and
    ``messages[message_index]`` target from that execution. Keeping the task
    messages in one file avoids copying them into this index.
    """
    out_file.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    targets_written = 0
    conversations = 0
    rows_by_source: Counter[str] = Counter()
    with out_file.open("w") as stream:
        for conversation, tasks in _selected(in_dir, config):
            metadata = build_metadata(
                conversation, metadata_key=config.metadata_key
            )
            wrote_conversation = False
            for task_index, messages in enumerate(tasks):
                summary = execution_summary_messages(messages)
                if not _task_selected(summary, config):
                    continue
                steps = _assistant_steps(messages)
                if not steps:
                    continue
                stream.write(json.dumps({
                    "schema_version": STEP_SCHEMA_VERSION,
                    "execution_id": _execution_id(metadata, task_index),
                    "assistant_steps": steps,
                    "task": {"index": task_index, "count": len(tasks)},
                    "execution": summary,
                    "metadata": metadata,
                }, ensure_ascii=False) + "\n")
                rows += 1
                targets_written += len(steps)
                wrote_conversation = True
                rows_by_source[metadata["source"]] += 1
            conversations += int(wrote_conversation)
    return {
        "rows": rows,
        "targets": targets_written,
        "conversations": conversations,
        "by_source": dict(rows_by_source),
    }
