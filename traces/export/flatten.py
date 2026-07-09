"""Flatten structured message fields into a single content string."""
from __future__ import annotations

import json
from typing import Any

# Fixed tag order for known structured fields. Deterministic.
# (Field name on the message dict, XML tag name, render function suffix.)
KNOWN_FIELDS: list[tuple[str, str]] = [
    ("tool_use", "tool_use"),
    ("tool_uses", "tool_use"),
    ("tool_results", "tool_result"),
    ("tool_result", "tool_result"),
    ("code_context", "code_context"),
    ("suggested_diffs", "suggested_diff"),
    ("diff_histories", "diff_history"),
]

# Fields that are message-level metadata, NOT training signal, kept out of content.
META_FIELDS = {"role", "content", "model", "timestamp", "session_id", "name"}


def _render_tool_use(item: dict[str, Any]) -> str:
    name = item.get("name", "")
    body = item.get("input", item)
    body_str = json.dumps(body, ensure_ascii=False, sort_keys=True)
    if name:
        return f'<tool_use name="{name}">\n{body_str}\n</tool_use>'
    return f"<tool_use>\n{body_str}\n</tool_use>"


def _render_tool_result(item: dict[str, Any]) -> str:
    rtype = item.get("type", "")
    text = item.get("text", json.dumps(item, ensure_ascii=False, sort_keys=True))
    if rtype:
        return f'<tool_result type="{rtype}">\n{text}\n</tool_result>'
    return f"<tool_result>\n{text}\n</tool_result>"


def _render_code_context(item: dict[str, Any]) -> str:
    file = item.get("file", item.get("path", ""))
    code = item.get("code", json.dumps(item, ensure_ascii=False, sort_keys=True))
    if file:
        return f'<code_context file="{file}">\n{code}\n</code_context>'
    return f"<code_context>\n{code}\n</code_context>"


def _render_suggested_diff(item: dict[str, Any]) -> str:
    file = item.get("file", item.get("path", ""))
    diff = item.get("diff", json.dumps(item, ensure_ascii=False, sort_keys=True))
    if file:
        return f'<suggested_diff file="{file}">\n{diff}\n</suggested_diff>'
    return f"<suggested_diff>\n{diff}\n</suggested_diff>"


def _render_diff_history(item: dict[str, Any]) -> str:
    return f"<diff_history>\n{json.dumps(item, ensure_ascii=False, sort_keys=True)}\n</diff_history>"


_RENDERERS = {
    "tool_use": _render_tool_use,
    "tool_result": _render_tool_result,
    "code_context": _render_code_context,
    "suggested_diff": _render_suggested_diff,
    "diff_history": _render_diff_history,
}


def _render_known(field_name: str, tag: str, value: Any) -> str:
    renderer = _RENDERERS[tag]
    if isinstance(value, list):
        return "\n\n".join(renderer(item) for item in value)
    return renderer(value)


def _render_unknown(field_name: str, value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return f'<field name="{field_name}">\n{body}\n</field>'


def flatten_message(msg: dict[str, Any]) -> str:
    """Render a message dict's content + structured fields into one string."""
    parts: list[str] = []
    content = msg.get("content", "")
    if content:
        parts.append(str(content))

    for field_name, tag in KNOWN_FIELDS:
        if field_name in msg and msg[field_name]:
            parts.append(_render_known(field_name, tag, msg[field_name]))

    # Unknown non-meta fields, sorted by key for determinism.
    unknown = sorted(k for k in msg.keys() if k not in META_FIELDS and k not in dict(KNOWN_FIELDS))
    for k in unknown:
        parts.append(_render_unknown(k, msg[k]))

    return "\n\n".join(parts)
