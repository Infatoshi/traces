"""DistillKit `messages` variant exporter."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from traces.config import ExportConfig
from traces.export.filters import dedup_key, filter_reason
from traces.export.flatten import flatten_message
from traces.export.metadata import build_metadata


def _iter_conversations(in_dir: Path):
    for src in sorted(in_dir.glob("*.jsonl")):
        with src.open() as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def export_messages(in_dir: Path, out_file: Path, config: ExportConfig) -> dict:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    dropped_no_assistant = 0
    dropped_filter = 0
    dropped_dedup = 0
    dropped_by_reason: Counter[str] = Counter()
    rows_by_source: Counter[str] = Counter()
    seen: set[str] = set()

    with out_file.open("w") as f:
        for conv in _iter_conversations(in_dir):
            reason = filter_reason(conv, config)
            if reason:
                dropped_filter += 1
                dropped_by_reason[reason] += 1
                continue
            if config.dedup:
                k = dedup_key(conv)
                if k in seen:
                    dropped_dedup += 1
                    continue
                seen.add(k)
            msgs = conv.get("messages", [])
            if not any(m.get("role") == "assistant" for m in msgs):
                dropped_no_assistant += 1
                continue
            flattened = [
                {"role": m.get("role", "user"), "content": flatten_message(m)}
                for m in msgs
            ]
            metadata = build_metadata(conv, metadata_key=config.metadata_key)
            f.write(
                json.dumps(
                    {"messages": flattened, "metadata": metadata},
                    ensure_ascii=False,
                )
                + "\n"
            )
            rows += 1
            rows_by_source[metadata["source"]] += 1

    return {
        "rows": rows,
        "dropped_no_assistant": dropped_no_assistant,
        "dropped_filter": dropped_filter,
        "dropped_dedup": dropped_dedup,
        "dropped_by_reason": dict(dropped_by_reason),
        "by_source": dict(rows_by_source),
    }
