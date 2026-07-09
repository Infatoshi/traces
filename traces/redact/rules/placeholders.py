"""Stable placeholder bookkeeping ([CATEGORY:NNNN:sha10])."""
from __future__ import annotations

import collections
import hashlib


class PlaceholderBook:
    """Tracks per-category counters and stable value->placeholder maps."""

    def __init__(self) -> None:
        self.counts: collections.Counter[str] = collections.Counter()
        self.maps: dict[str, dict[str, str]] = collections.defaultdict(dict)

    def stable(self, category: str, value: str) -> str:
        bucket = self.maps[category]
        if value not in bucket:
            digest = hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:10]
            bucket[value] = f"[{category.upper()}:{len(bucket) + 1:04d}:{digest}]"
        return bucket[value]
