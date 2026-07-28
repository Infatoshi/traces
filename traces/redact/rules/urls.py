"""URL redaction rules.

Patterns ported verbatim from RodriMora/agent-trace-redaction-methodology
`scripts/export_redacted_traces.py` Redactor.__init__ (the opencode_share_url,
private_url, and url entries). The allowlist short-circuit for the `url` category
is performed in `redact_string` (rules/__init__.py), matching upstream's
`is_allowed_url` behaviour.
"""
from __future__ import annotations

import re

from traces.redact.rules._types import Rule, RuleContext


def build(ctx: RuleContext) -> list[Rule]:
    return [
        (
            "opencode_share_url",
            re.compile(r"\bhttps?://(?:www\.)?opncd\.ai/share/[^\s'\"<>`)}\]]+", re.IGNORECASE),
            "[PRIVATE_SHARE_URL]",
        ),
        (
            "private_url",
            re.compile(r"\bhttps?://(?:localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[0-1])\.\d+\.\d+)[^\s'\"<>`]*", re.IGNORECASE),
            "[PRIVATE_URL]",
        ),
        ("url", re.compile(r"\bhttps?://[^\s'\"<>`)}\]]+", re.IGNORECASE), None),
    ]
