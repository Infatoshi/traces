"""Merge export trees by ``metadata.trace_id`` (e.g. a new export with the published dataset).

Local agent stores get pruned, so a fresh export is not a superset of what was
published. ``merge_exports`` keeps every trace from every input: for a trace in
several inputs it keeps the version with the most messages (the earlier input on
ties, so pass the newest first) and takes all four variants from that version.
An input dir may hold ``idmap.json`` mapping its trace IDs onto stable ones (for
revisions exported under a different metadata key).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

VARIANTS = ("messages.jsonl", "sharegpt.jsonl", "execution.jsonl", "assistant_steps.jsonl")
_EXECUTION_ID = re.compile(rb'"execution_id":\s*"([^"]+)"')


def tail_metadata(line: bytes) -> dict[str, Any]:
    """Parse only the trailing ``metadata`` object (exports write it last)."""
    for marker in (b'"metadata": {', b'"metadata":{'):
        pos = line.rfind(marker)
        if pos != -1:
            tail = line[pos + len(marker) - 1:].rstrip()
            if tail.endswith(b"}"):
                try:
                    return json.loads(tail[:-1])
                except ValueError:
                    pass
    return json.loads(line)["metadata"]


def iter_rows(path: Path) -> Iterator[tuple[bytes, dict[str, Any]]]:
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                yield line, tail_metadata(line)


def index_messages(export_dir: Path) -> dict[str, int]:
    """trace_id -> message count, from messages.jsonl."""
    out: dict[str, int] = {}
    for _line, md in iter_rows(export_dir / "messages.jsonl"):
        out[md["trace_id"]] = int((md.get("counts") or {}).get("messages") or 0)
    return out


def merge_exports(out: Path, inputs: list[Path]) -> dict[str, Any]:
    maps = [
        json.loads((d / "idmap.json").read_text()) if (d / "idmap.json").exists() else {}
        for d in inputs
    ]
    best: dict[str, tuple[tuple[int, int], int, str]] = {}
    present: dict[str, set[int]] = {}
    for i, d in enumerate(inputs):
        for tid, msgs in index_messages(d).items():
            sid = maps[i].get(tid, tid)
            present.setdefault(sid, set()).add(i)
            cand = (msgs, -i)
            if sid not in best or cand > best[sid][0]:
                best[sid] = (cand, i, tid)
    winners = {(i, tid): sid for sid, (_c, i, tid) in best.items()}
    report: dict[str, Any] = {
        "inputs": [str(d) for d in inputs],
        "traces": len(best),
        "winners_by_input": {
            str(inputs[i]): n for i, n in sorted(Counter(i for i, _t in winners).items())
        },
        "older_version_kept_because_longer": sum(
            1 for sid, (_c, i, _t) in best.items() if any(j < i for j in present[sid])
        ),
        "remapped_ids": sum(1 for (i, tid), sid in winners.items() if sid != tid),
        "variants": {},
    }
    out.mkdir(parents=True, exist_ok=True)
    for variant in VARIANTS:
        rows = 0
        by_source: Counter[str] = Counter()
        seen: set[bytes] = set()
        with (out / variant).open("wb") as target:
            for i, d in enumerate(inputs):
                path = d / variant
                if not path.exists():
                    continue
                for line, md in iter_rows(path):
                    tid = md["trace_id"]
                    sid = winners.get((i, tid))
                    if sid is None:
                        continue
                    if sid != tid:
                        line = line.replace(tid.encode(), sid.encode())
                    m = _EXECUTION_ID.search(line[:400])
                    if m:
                        if m.group(1) in seen:
                            continue
                        seen.add(m.group(1))
                    target.write(line if line.endswith(b"\n") else line + b"\n")
                    rows += 1
                    by_source[md.get("source") or "unknown"] += 1
        report["variants"][variant] = {"rows": rows, "by_source": dict(sorted(by_source.items()))}
    return report


def pull_published(repo_id: str, out: Path, revision: str | None = None) -> dict[str, Any]:
    """Download the published variants of a dataset revision into ``out``."""
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    info = api.dataset_info(repo_id, revision=revision)
    present = {s.rfilename for s in info.siblings or []}
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for name in (*VARIANTS, "MANIFEST.json"):
        if name in present:
            hf_hub_download(repo_id, name, repo_type="dataset", revision=info.sha, local_dir=out)
            files.append(name)
    return {"repo": repo_id, "revision": info.sha, "files": files}


def keep_traces(tree: Path, ids: set[str], out: Path) -> dict[str, int]:
    """Copy only the rows of the given traces from every variant in ``tree`` into ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    kept: dict[str, int] = {}
    for path in sorted(tree.glob("*.jsonl")):
        n = 0
        with (out / path.name).open("wb") as target:
            for line, md in iter_rows(path):
                if md.get("trace_id") in ids:
                    target.write(line if line.endswith(b"\n") else line + b"\n")
                    n += 1
        kept[path.name] = n
    return kept


def drop_traces(tree: Path, ids: set[str]) -> dict[str, int]:
    """Remove every row of the given traces from every variant in ``tree``."""
    removed: dict[str, int] = {}
    if not ids:
        return removed
    for path in sorted(tree.glob("*.jsonl")):
        tmp = path.with_name(f".{path.name}.drop")
        n = 0
        with tmp.open("wb") as out:
            for line, md in iter_rows(path):
                if md.get("trace_id") in ids:
                    n += 1
                    continue
                out.write(line)
        tmp.replace(path)
        removed[path.name] = n
    return removed
