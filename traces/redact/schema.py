"""Schema-aware recursive redaction."""
from __future__ import annotations

import re
from typing import Any, Callable

from traces.redact.rules.constants import (
    SENSITIVE_FIELD_EXACT,
    SENSITIVE_FIELD_SUBSTRINGS,
)
from traces.redact.rules.placeholders import PlaceholderBook


def normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def sensitive_field_category(key: str) -> str | None:
    normalized = normalize_key(key)
    compact = normalized.replace("_", "")
    if normalized in SENSITIVE_FIELD_EXACT or compact in SENSITIVE_FIELD_EXACT:
        return "FIELD"
    if any(frag in normalized or frag in compact for frag in SENSITIVE_FIELD_SUBSTRINGS):
        return "FIELD"
    return None


def redact_sensitive_field(
    key: str,
    value: Any,
    redact_string: Callable[[str], str],
    book: PlaceholderBook,
) -> Any:
    if sensitive_field_category(key) is None:
        return redact_obj(value, redact_string, book)
    book.counts["sensitive_field_" + normalize_key(key)] += 1
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (dict, list)):
        return "[REDACTED:SENSITIVE_FIELD]"
    if re.search(r"(?i)encrypted|signature", key):
        return "[REDACTED:OPAQUE_PROVIDER_BLOB]"
    if re.search(r"(?i)share", key):
        return "[REDACTED:SHARE_METADATA]"
    return "[REDACTED:SENSITIVE_FIELD]"


def redact_obj(
    value: Any,
    redact_string: Callable[[str], str],
    book: PlaceholderBook,
) -> Any:
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, list):
        return [redact_obj(v, redact_string, book) for v in value]
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            new_key = redact_string(key) if isinstance(key, str) else key
            if isinstance(key, str):
                redacted[new_key] = redact_sensitive_field(key, item, redact_string, book)
            else:
                redacted[new_key] = redact_obj(item, redact_string, book)
        return redacted
    return value
