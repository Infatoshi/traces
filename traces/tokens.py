"""Token counts for an export's messages.jsonl (o200k_base, the GPT-4o tokenizer).

Counts every string in each message except the role. A trace's tokens are
credited to the models in its metadata, split evenly when there are several;
``assistant`` is the part the models wrote. Runs over newline-aligned byte
ranges in a process pool.
"""
from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from traces.parallel import iter_range, line_ranges

ENCODING = "o200k_base"
_ENC = None


def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for item in obj:
            yield from _strings(item)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if key != "role":
                yield from _strings(value)


def _encode_len(text: str) -> int | None:
    try:
        return len(_ENC.encode_ordinary(text))
    except BaseException:  # tiktoken panics on lone surrogates and some very long runs
        return None


def count_text(text: str) -> tuple[int, int]:
    """(tokens, chars estimated at 4 chars/token because the tokenizer failed)."""
    n = _encode_len(text)
    if n is not None:
        return n, 0
    clean = text.encode("utf-8", "replace").decode("utf-8")
    total = approx = 0
    for i in range(0, len(clean), 4000):
        chunk = clean[i:i + 4000]
        n = _encode_len(chunk)
        if n is None:
            approx += len(chunk)
            n = len(chunk) // 4
        total += n
    return total, approx


def _count_range(task: tuple[str, int, int]) -> dict[str, Any]:
    global _ENC
    if _ENC is None:
        import tiktoken

        _ENC = tiktoken.get_encoding(ENCODING)
    path, start, end = task
    by_model: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    by_source: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    ids: list[str] = []
    total = assistant = approx = 0
    for line in iter_range(Path(path), start, end):
        row = json.loads(line)
        md = row.get("metadata") or {}
        t = a = 0
        for message in row.get("messages") or []:
            n = 0
            for text in _strings(message):
                k, ap = count_text(text)
                n += k
                approx += ap
            t += n
            if isinstance(message, dict) and message.get("role") == "assistant":
                a += n
        models = md.get("models") or ["(none recorded)"]
        for model in models:
            entry = by_model[model]
            entry[0] += 1 / len(models)
            entry[1] += t / len(models)
            entry[2] += a / len(models)
        src = by_source[md.get("source") or "unknown"]
        src[0] += 1
        src[1] += t
        src[2] += a
        ids.append(md.get("trace_id"))
        total += t
        assistant += a
    return {"by_model": dict(by_model), "by_source": dict(by_source), "ids": ids,
            "total": total, "assistant": assistant, "approx_chars": approx}


def token_stats(export_dir: Path, jobs: int) -> tuple[dict[str, Any], set[str]]:
    """Token summary for ``export_dir/messages.jsonl`` plus the set of trace IDs."""
    path = export_dir / "messages.jsonl"
    tasks = [(str(path), s, e) for s, e in line_ranges(path)]
    by_model: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    by_source: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    ids: set[str] = set()
    total = assistant = approx = 0
    with ProcessPoolExecutor(max(1, min(jobs, len(tasks) or 1))) as pool:
        for part in pool.map(_count_range, tasks):
            for name, (n, t, a) in part["by_model"].items():
                entry = by_model[name]
                entry[0] += n
                entry[1] += t
                entry[2] += a
            for name, (n, t, a) in part["by_source"].items():
                entry = by_source[name]
                entry[0] += n
                entry[1] += t
                entry[2] += a
            ids.update(part["ids"])
            total += part["total"]
            assistant += part["assistant"]
            approx += part["approx_chars"]

    def table(d):
        return {k: {"traces": round(v[0], 1), "tokens": round(v[1]), "assistant_tokens": round(v[2])}
                for k, v in sorted(d.items(), key=lambda kv: -kv[1][1])}

    return {
        "encoding": ENCODING,
        "tokens": total,
        "assistant_tokens": assistant,
        "estimated_chars": approx,
        "by_model": table(by_model),
        "by_source": table(by_source),
    }, ids
