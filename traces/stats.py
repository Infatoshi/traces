# traces/stats.py
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def corpus_stats(in_dir: Path) -> dict:
    sources = Counter()
    turn_counts = []
    asst_chars = 0
    for src in sorted(in_dir.glob("*.jsonl")):
        with src.open() as f:
            for line in f:
                if not line.strip():
                    continue
                conv = json.loads(line)
                sources[conv.get("source", src.stem)] += 1
                turn_counts.append(len(conv.get("messages", [])))
                asst_chars += sum(len(m.get("content", "")) for m in conv.get("messages", []) if m.get("role") == "assistant")
    return {
        "conversations_by_source": dict(sources),
        "total_conversations": sum(sources.values()),
        "turn_count_min": min(turn_counts) if turn_counts else 0,
        "turn_count_max": max(turn_counts) if turn_counts else 0,
        "turn_count_mean": (sum(turn_counts) / len(turn_counts)) if turn_counts else 0,
        "total_assistant_chars": asst_chars,
    }
