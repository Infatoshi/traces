"""Corpus statistics for exported traces datasets."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def corpus_stats(in_dir: Path) -> dict:
    sources: Counter[str] = Counter()
    models: Counter[str] = Counter()
    token_usage: Counter[str] = Counter()
    turn_counts: list[int] = []
    seen_traces: set[str] = set()
    total_rows = 0
    assistant_chars = 0
    traces_with_tools = 0
    traces_with_diffs = 0

    for path in sorted(in_dir.glob("*.jsonl")):
        with path.open() as handle:
            for row_index, line in enumerate(handle):
                if not line.strip():
                    continue
                total_rows += 1
                row = json.loads(line)
                metadata = row.get("metadata")
                if not isinstance(metadata, dict):
                    source = str(row.get("source") or path.stem)
                    sources[source] += 1
                    messages = row.get("messages") or []
                    turn_counts.append(len(messages))
                    assistant_chars += sum(
                        len(str(message.get("content") or ""))
                        for message in messages
                        if isinstance(message, dict)
                        and message.get("role") == "assistant"
                    )
                    continue
                trace_id = str(metadata.get("trace_id") or f"legacy:{path}:{row_index}")
                if trace_id in seen_traces:
                    continue
                seen_traces.add(trace_id)
                source = str(metadata.get("source") or "unknown")
                sources[source] += 1
                counts = metadata.get("counts") or {}
                message_count = int(counts.get("messages") or 0)
                turn_counts.append(message_count)
                assistant_chars += int(counts.get("assistant_chars") or 0)
                traces_with_tools += int(int(counts.get("tool_calls") or 0) > 0)
                traces_with_diffs += int(int(counts.get("diffs") or 0) > 0)
                models.update(metadata.get("models") or [])
                token_usage.update(metadata.get("token_usage") or {})

    return {
        "conversations_by_source": dict(sources),
        "conversations_by_model": dict(models),
        "total_conversations": sum(sources.values()),
        "total_rows": total_rows,
        "turn_count_min": min(turn_counts) if turn_counts else 0,
        "turn_count_max": max(turn_counts) if turn_counts else 0,
        "turn_count_mean": sum(turn_counts) / len(turn_counts) if turn_counts else 0,
        "total_assistant_chars": assistant_chars,
        "traces_with_tools": traces_with_tools,
        "traces_with_diffs": traces_with_diffs,
        "token_usage": dict(token_usage),
    }
