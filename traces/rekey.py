"""One-time rewrite of legacy placeholders to the keyed form.

Exports before the keyed placeholders used ``[CATEGORY:NNNN:sha10]``, where
``sha10`` is the first 10 hex digits of a plain SHA-256 of the redacted value.
Anyone can hash guessed emails or home paths and match them. This rewrites each
one to ``[CATEGORY:hmac10]`` with ``hmac10 = HMAC(metadata key, domain + sha10)``.

The original value is gone, so the new digest is keyed on the old digest: the
same legacy value still maps to the same placeholder across rows, and it can no
longer be matched by hashing guesses. It will not equal the placeholder a fresh
redaction of that value produces. The rewrite is byte-level (placeholder
characters never need JSON escaping), idempotent, and touches nothing else.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from traces.parallel import iter_range, line_ranges

LEGACY_RE = re.compile(rb"\[([A-Z][A-Z0-9_]*):\d{4,}:([0-9a-f]{10})\]")
_DOMAIN = b"traces-legacy-placeholder\x00"

_KEY: bytes | None = None


def rekey_digest(key: bytes, legacy_digest: bytes) -> str:
    return hmac.new(key, _DOMAIN + legacy_digest, hashlib.sha256).hexdigest()[:10]


def rekey_line(line: bytes, key: bytes, hits: Counter[str] | None = None) -> bytes:
    def repl(m: re.Match[bytes]) -> bytes:
        if hits is not None:
            hits[m.group(1).decode()] += 1
        return b"[" + m.group(1) + b":" + rekey_digest(key, m.group(2)).encode() + b"]"

    return LEGACY_RE.sub(repl, line)


def _init(key: bytes) -> None:
    global _KEY
    _KEY = key


def _rekey_range(task: tuple[str, int, int, str | None]) -> dict[str, int]:
    path, start, end, part = task
    assert _KEY is not None
    hits: Counter[str] = Counter()
    if part is None:
        for line in iter_range(Path(path), start, end):
            rekey_line(line, _KEY, hits)
        return dict(hits)
    with open(part, "wb") as out:
        for line in iter_range(Path(path), start, end):
            out.write(rekey_line(line, _KEY, hits))
    return dict(hits)


def rekey_tree(root: Path, key: str | bytes, fix: bool = True, jobs: int | None = None) -> dict[str, Any]:
    """Count (and with ``fix``, rewrite in place) legacy placeholders in every *.jsonl."""
    if not key:
        raise ValueError("rekey needs the stable metadata key; a random key would break consistency")
    kb = key.encode() if isinstance(key, str) else key
    tasks, plan = [], []
    for path in sorted(root.rglob("*.jsonl")):
        parts = []
        for index, (start, end) in enumerate(line_ranges(path)):
            part = str(path.with_name(f".{path.name}.rekey{index:05d}")) if fix else None
            tasks.append((str(path), start, end, part))
            parts.append(part)
        plan.append((path, parts))
    by_category: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    if tasks:
        workers = max(1, min(jobs or os.cpu_count() or 1, len(tasks)))
        with ProcessPoolExecutor(workers, initializer=_init, initargs=(kb,)) as pool:
            for (path, *_rest), hits in zip(tasks, pool.map(_rekey_range, tasks)):
                by_category.update(hits)
                by_file[str(Path(path).relative_to(root))] += sum(hits.values())
    if fix:
        for path, parts in plan:
            tmp = path.with_name(f".{path.name}.rekeyed")
            with tmp.open("wb") as target:
                for part in parts:
                    with open(part, "rb") as source:
                        while chunk := source.read(1 << 24):
                            target.write(chunk)
                    os.unlink(part)
            os.replace(tmp, path)
    return {
        "occurrences": sum(by_category.values()),
        "by_file": {k: v for k, v in by_file.items() if v},
        "by_category": dict(by_category.most_common()),
    }
