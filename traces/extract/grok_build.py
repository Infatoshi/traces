"""Grok Build (xAI TUI) extractor.

Reads ``~/.grok/sessions/<url-encoded-cwd>/<session-id>/chat_history.jsonl``
with optional ``summary.json`` metadata. Format is documented in the Grok
README under session storage.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

from traces.extract.base import Conversation, Message


def find_grok_installations(home: Path | None = None) -> list[Path]:
    """Find Grok Build home directories (contain a ``sessions/`` tree)."""
    home = home or Path.home()
    locations: list[Path] = []

    candidates = [
        home / ".grok",
        Path(os.environ["GROK_HOME"]).expanduser() if os.environ.get("GROK_HOME") else None,
    ]
    for cand in candidates:
        if cand is None:
            continue
        if (cand / "sessions").is_dir() or cand.is_dir():
            locations.append(cand)

    return list({p.resolve() for p in locations if p.exists()})


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" or "text" in item:
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "summary_text":
                    parts.append(str(item.get("text") or ""))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def _reasoning_text(obj: dict[str, Any]) -> str:
    summary = obj.get("summary")
    if summary is not None:
        text = _content_to_text(summary)
        if text.strip():
            return text
    return _content_to_text(obj.get("content"))


def _tool_calls_to_uses(tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(tool_calls, list):
        return []
    uses: list[dict[str, Any]] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = tc.get("name") or tc.get("function", {}).get("name") or ""
        raw_args = tc.get("arguments")
        if raw_args is None and isinstance(tc.get("function"), dict):
            raw_args = tc["function"].get("arguments")
        parsed: Any = raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed = {"_raw": raw_args}
        uses.append({
            "id": tc.get("id"),
            "name": name,
            "input": parsed if parsed is not None else {},
        })
    return uses


def extract_grok_session(session_dir: Path) -> Conversation | None:
    """Extract one session directory into a Conversation, or None if empty."""
    chat = session_dir / "chat_history.jsonl"
    if not chat.is_file():
        return None

    summary: dict[str, Any] = {}
    summary_path = session_dir / "summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text())
        except (json.JSONDecodeError, OSError):
            summary = {}

    messages: list[Message] = []
    with chat.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            msg_type = obj.get("type") or obj.get("role") or ""
            if msg_type == "system":
                text = _content_to_text(obj.get("content"))
                if text:
                    messages.append({"role": "system", "content": text})
            elif msg_type == "user":
                text = _content_to_text(obj.get("content"))
                messages.append({"role": "user", "content": text})
            elif msg_type == "assistant":
                msg: Message = {
                    "role": "assistant",
                    "content": _content_to_text(obj.get("content")),
                }
                if obj.get("model_id"):
                    msg["model"] = str(obj["model_id"])
                uses = _tool_calls_to_uses(obj.get("tool_calls"))
                if uses:
                    msg["tool_uses"] = uses
                messages.append(msg)
            elif msg_type == "reasoning":
                text = _reasoning_text(obj)
                if not text.strip():
                    continue
                msg = {
                    "role": "assistant",
                    "content": f"<reasoning>\n{text}\n</reasoning>",
                }
                if obj.get("model_id"):
                    msg["model"] = str(obj["model_id"])
                messages.append(msg)
            elif msg_type == "tool_result":
                text = _content_to_text(obj.get("content"))
                msg = {"role": "tool", "content": text}
                if obj.get("tool_call_id"):
                    msg["tool_use"] = {
                        "id": obj["tool_call_id"],
                        "type": "tool_result",
                        "text": text,
                    }
                messages.append(msg)
            else:
                # Unknown types: keep as user-ish content only if non-empty
                text = _content_to_text(obj.get("content"))
                if text.strip():
                    messages.append({"role": "user", "content": text})

    if not messages:
        return None

    info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
    session_id = (
        str(info.get("id") or summary.get("id") or session_dir.name)
    )
    cwd = info.get("cwd")
    if not cwd:
        # Parent dir is URL-encoded cwd
        try:
            cwd = unquote(session_dir.parent.name)
        except Exception:
            cwd = None

    conv: Conversation = {
        "messages": messages,
        "source": "grok_build",
        "session_id": session_id,
        "source_file": str(chat),
        "project_path": cwd,
        "project_name": Path(cwd).name if cwd else None,
        "name": summary.get("generated_title") or summary.get("session_summary"),
    }
    for source_key, target_key in (
        ("created_at", "created_at"),
        ("updated_at", "updated_at"),
        ("chat_format_version", "version"),
        ("current_model_id", "model"),
        ("reasoning_effort", "reasoning_effort"),
        ("session_kind", "session_kind"),
        ("sandbox_profile", "sandbox_profile"),
    ):
        value = summary.get(source_key)
        if isinstance(value, (str, int, float, bool)) and value != "":
            conv[target_key] = value
    return conv


def extract_grok_conversations(installation: Path) -> list[Conversation]:
    """Extract all sessions under a Grok home's ``sessions/`` tree."""
    sessions_root = installation / "sessions"
    if not sessions_root.is_dir():
        return []

    conversations: list[Conversation] = []
    # chat_history.jsonl lives at sessions/<cwd>/<session-id>/chat_history.jsonl
    for chat in sorted(sessions_root.rglob("chat_history.jsonl")):
        # Skip anything under subagents nested copies if present as nested chat_history
        # (primary path is one chat_history per session dir)
        conv = extract_grok_session(chat.parent)
        if conv:
            conv["installation"] = str(installation)
            conversations.append(conv)
    return conversations


class GrokBuildExtractor:
    source = "grok_build"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def find_installations(self) -> list[Path]:
        return find_grok_installations(self.root)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        yield from extract_grok_conversations(install_dir)
