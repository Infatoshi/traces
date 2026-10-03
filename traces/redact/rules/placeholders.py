"""Stable placeholders: ``[CATEGORY:hmac10]``.

The digest is an HMAC of the matched value under the metadata key, so the same
value always gets the same placeholder (within a trace, across traces, across
runs, and regardless of worker count), and low-entropy values such as emails or
home paths cannot be recovered by hashing guesses without the key.
"""
from __future__ import annotations

import collections
import hashlib
import hmac

_DOMAIN = b"traces-placeholder\x00"


class PlaceholderBook:
    """Per-category counters plus keyed value->placeholder mapping."""

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            from traces.export.metadata import _key_bytes

            key = _key_bytes(None)
        self.key = key
        self.counts: collections.Counter[str] = collections.Counter()

    def stable(self, category: str, value: str) -> str:
        digest = hmac.new(
            self.key, _DOMAIN + value.encode("utf-8", "ignore"), hashlib.sha256
        ).hexdigest()[:10]
        return f"[{category.upper()}:{digest}]"
