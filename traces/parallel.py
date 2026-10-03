"""Split JSONL files into newline-aligned byte ranges for process pools."""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

RANGE_BYTES = 32_000_000


def default_jobs() -> int:
    return os.cpu_count() or 1


def line_ranges(path: Path, target: int = RANGE_BYTES) -> list[tuple[int, int]]:
    """Byte ranges of about ``target`` bytes that start and end on line breaks."""
    size = path.stat().st_size
    ranges: list[tuple[int, int]] = []
    start = 0
    with path.open("rb") as f:
        while start < size:
            end = start + target
            if end < size:
                f.seek(end)
                f.readline()
                end = f.tell()
            else:
                end = size
            ranges.append((start, end))
            start = end
    return ranges


def iter_range(path: Path, start: int, end: int) -> Iterator[bytes]:
    with path.open("rb") as f:
        f.seek(start)
        pos = start
        while pos < end:
            line = f.readline()
            if not line:
                break
            pos += len(line)
            yield line
