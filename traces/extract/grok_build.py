"""Grok Build (xAI TUI) extractor.

Reads ``~/.grok/sessions/<url-encoded-cwd>/<session-id>/chat_history.jsonl``
with optional ``summary.json`` metadata. Format is documented in the Grok
README under session storage.

``chat_history.jsonl`` is the model's current context window, so after an
auto-compaction it holds only the summary and the turns since. The same
session's ``updates.jsonl`` is the append-only ACP event stream (every prompt,
reasoning chunk, tool call and tool result), so when it covers more of the
session the conversation is rebuilt from it instead.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
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
                if item.get("type") == "text" or "text" in item or item.get("type") == "summary_text":
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
                parsed = raw_args
        uses.append({
            "id": tc.get("id"),
            "name": name,
            "input": parsed if parsed is not None else {},
        })
    return uses


# chat_history user entries that are session context, not prompts.
_CONTEXT_PREFIXES = ("<user_info>", "<system-reminder>")
_COMPACTION_SUMMARY = "This session is being continued"
# rawOutput fields holding the exact string the model was shown.
_PROMPT_TEXT_KEYS = ("output_for_prompt", "tool_output_for_prompt", "summary_for_prompt")
_BYTE_FIELDS = ("stdout", "stderr", "output")


def _decode_bytes(value: Any) -> str | None:
    """Grok writes some tool output (Bash ``output``, grep ``stdout``) as a list of byte values."""
    if isinstance(value, list) and value and all(isinstance(b, int) and 0 <= b < 256 for b in value):
        return bytes(value).decode("utf-8", errors="replace")
    return None


def _prompt_text(raw: Any, depth: int = 0) -> str | None:
    if depth > 4 or not isinstance(raw, dict):
        return None
    for key in _PROMPT_TEXT_KEYS:
        if isinstance(raw.get(key), str):
            return raw[key]
    for value in raw.values():
        found = _prompt_text(value, depth + 1)
        if found is not None:
            return found
    return None


def _plain(value: Any) -> Any:
    """JSON-safe copy of a rawOutput: byte lists decoded, inline image data dropped."""
    if isinstance(value, dict):
        if isinstance(value.get("data"), str) and len(value["data"]) > 256:
            return "[image]"
        out: dict[str, Any] = {}
        for key, item in value.items():
            decoded = _decode_bytes(item) if key in _BYTE_FIELDS else None
            out[key] = decoded if decoded is not None else _plain(item)
        return out
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _update_content_text(content: Any) -> str | None:
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "content" and isinstance(item.get("content"), dict):
            inner = item["content"]
            parts.append("[image]" if inner.get("type") == "image" else str(inner.get("text") or ""))
        elif kind == "diff":
            parts.append(
                f"{item.get('path') or ''}\n--- old\n{item.get('oldText') or ''}\n+++ new\n{item.get('newText') or ''}"
            )
    return "\n".join(parts) if parts else None


def _tool_output_text(update: dict[str, Any], call_input: Any = None) -> str:
    """Model-facing text of a finished tool call, as chat_history would have recorded it."""
    raw = update.get("rawOutput")
    text = _prompt_text(raw)
    if text is not None:
        return text
    if isinstance(raw, dict):
        for key in _BYTE_FIELDS:
            decoded = _decode_bytes(raw.get(key))
            if decoded is not None:
                return decoded
        if "ImageContent" in raw:
            path = call_input.get("file_path") or call_input.get("target_file") if isinstance(call_input, dict) else None
            return f"Read image file: {path}" if path else "[image]"
        for value in raw.values():
            if isinstance(value, dict) and isinstance(value.get("content"), str):
                return value["content"]
    text = _update_content_text(update.get("content"))
    if text is not None:
        return text
    if raw is None:
        return ""
    return json.dumps(_plain(raw), ensure_ascii=False)


def _chunk_text(content: Any) -> str:
    if isinstance(content, dict) and content.get("type") == "image":
        return "[image]"
    return _content_to_text(content)


def _checkpoint_summary(session_dir: Path, checkpoint_file: Any) -> str | None:
    """The summary a compaction checkpoint put in place of the older turns."""
    path = session_dir / "compaction_checkpoints" / Path(str(checkpoint_file or "")).name
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    history = data.get("compacted_history") if isinstance(data, dict) else None
    for item in history if isinstance(history, list) else []:
        if isinstance(item, dict) and item.get("type") == "user":
            text = _content_to_text(item.get("content"))
            if text.startswith(_COMPACTION_SUMMARY):
                return text
    return None


def _messages_from_updates(path: Path, session_dir: Path | None = None) -> list[Message]:
    """Rebuild a session from its ``updates.jsonl`` ACP event stream.

    Typed prompts are wrapped in ``<user_query>`` tags as chat_history records
    them; hidden prompts (background-task and monitor notices) are kept verbatim.
    Each compaction leaves its summary as a user message at the point it
    happened, so the turns before it are marked as outside the later context.
    """
    messages: list[Message] = []
    prompt_starts: dict[Any, int] = {}
    thought: list[str] = []
    text: list[str] = []
    collecting: Message | None = None  # assistant message that tool calls attach to
    finished: set[str] = set()
    call_inputs: dict[str, Any] = {}
    typed: list[Message] = []  # prompts to wrap once all their chunks are in
    model: str | None = None
    turn_start = 0
    last_prompt: Any = None

    def assistant(content: str, **extra: Any) -> Message:
        msg: Message = {"role": "assistant", "content": content, **extra}
        if model:
            msg["model"] = model
        messages.append(msg)
        return msg

    def flush() -> None:
        nonlocal collecting
        if thought:
            assistant(f"<reasoning>\n{''.join(thought)}\n</reasoning>", phase="reasoning")
            thought.clear()
            collecting = None
        if text:
            collecting = assistant("".join(text))
            text.clear()

    with path.open(errors="replace") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            params = obj.get("params") if isinstance(obj, dict) else None
            update = params.get("update") if isinstance(params, dict) else None
            if not isinstance(update, dict):
                continue
            kind = update.get("sessionUpdate")
            meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}

            if kind == "user_message_chunk":
                flush()
                collecting = None
                prompt = meta.get("promptIndex")
                if meta.get("modelId"):
                    model = str(meta["modelId"])
                chunk = _chunk_text(update.get("content"))
                if messages and messages[-1]["role"] == "user" and prompt is not None and prompt == last_prompt:
                    messages[-1]["content"] += chunk
                    continue
                if prompt is not None:
                    prompt_starts[prompt] = len(messages)
                turn_start = len(messages)
                last_prompt = prompt
                messages.append({"role": "user", "content": chunk})
                if not meta.get("hideFromScrollback"):
                    typed.append(messages[-1])
            elif kind == "agent_thought_chunk":
                if text:
                    flush()
                thought.append(_chunk_text(update.get("content")))
            elif kind == "agent_message_chunk":
                if thought:
                    flush()
                text.append(_chunk_text(update.get("content")))
            elif kind == "tool_call":
                flush()
                if collecting is None:
                    collecting = assistant("")
                raw_input = update.get("rawInput")
                if update.get("toolCallId"):
                    call_inputs[update["toolCallId"]] = raw_input
                collecting.setdefault("tool_uses", []).append({
                    "id": update.get("toolCallId"),
                    "name": str(update.get("title") or ""),
                    "input": raw_input if raw_input is not None else {},
                })
            elif kind == "tool_call_update" and update.get("status") in ("completed", "failed"):
                call_id = update.get("toolCallId")
                if call_id in finished:
                    continue
                flush()
                collecting = None
                if call_id:
                    finished.add(call_id)
                output = _tool_output_text(update, call_inputs.get(call_id))
                messages.append({
                    "role": "tool",
                    "content": output,
                    "tool_result": {
                        "tool_call_id": call_id,
                        "output": output,
                        "status": "error" if update.get("status") == "failed" else "completed",
                    },
                })
            elif kind == "rewind_marker":
                # The user rewound to this prompt: everything from it onward was discarded.
                target = update.get("target_prompt_index")
                if target in prompt_starts:
                    thought.clear()
                    text.clear()
                    collecting = None
                    cut = prompt_starts[target]
                    del messages[cut:]
                    prompt_starts = {p: i for p, i in prompt_starts.items() if i < cut}
                    turn_start = min(turn_start, cut)
                    last_prompt = None
            elif kind == "compaction_checkpoint" and session_dir is not None:
                flush()
                collecting = None
                summary_text = _checkpoint_summary(session_dir, update.get("checkpoint_file"))
                if summary_text:
                    messages.append({"role": "user", "content": summary_text})
            elif kind == "turn_completed":
                flush()
                usage = update.get("usage") if isinstance(update.get("usage"), dict) else {}
                served = usage.get("modelUsage") if isinstance(usage.get("modelUsage"), dict) else {}
                if len(served) == 1:
                    name = str(next(iter(served)))
                    for msg in messages[turn_start:]:
                        if msg["role"] == "assistant":
                            msg["model"] = name
    flush()
    kept = {id(m) for m in messages}
    for msg in typed:
        if id(msg) in kept and not msg["content"].startswith(_CONTEXT_PREFIXES):
            msg["content"] = f"<user_query>\n{msg['content']}\n</user_query>"
    return messages


def _count(messages: list[Message], role: str) -> int:
    return sum(1 for m in messages if m.get("role") == role)


def _is_query(message: Message) -> bool:
    content = message.get("content")
    return message.get("role") == "user" and isinstance(content, str) and content.startswith("<user_query>")


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
    prompts = 0  # real prompts, not synthetic context or notices
    first_prompt: int | None = None
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
                if not obj.get("synthetic_reason") and not text.startswith(_CONTEXT_PREFIXES):
                    prompts += 1
                    if first_prompt is None:
                        first_prompt = len(messages)
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
                    "phase": "reasoning",
                }
                if obj.get("model_id"):
                    msg["model"] = str(obj["model_id"])
                messages.append(msg)
            elif msg_type == "tool_result":
                text = _content_to_text(obj.get("content"))
                msg = {"role": "tool", "content": text}
                if obj.get("tool_call_id"):
                    msg["tool_result"] = {
                        "tool_call_id": obj["tool_call_id"],
                        "output": obj.get("content"),
                    }
                messages.append(msg)
            else:
                # Unknown types: keep as user-ish content only if non-empty
                text = _content_to_text(obj.get("content"))
                if text.strip():
                    messages.append({"role": "user", "content": text})

    # Prefer the event stream only when it covers strictly more of the session
    # (a compacted chat_history); otherwise chat_history is the exact record.
    # The stream has no system prompt or session-start context, so those come
    # from chat_history: everything before its first real prompt.
    source_file = chat
    updates = session_dir / "updates.jsonl"
    if updates.is_file():
        streamed = _messages_from_updates(updates, session_dir)
        tools, queries = _count(streamed, "tool"), sum(map(_is_query, streamed))
        chat_tools = _count(messages, "tool")
        if tools >= chat_tools and queries >= prompts and (tools > chat_tools or queries > prompts):
            context = messages[: first_prompt if first_prompt is not None else len(messages)]
            # The stream re-inserts every compaction summary where it happened.
            messages = [m for m in context if not str(m.get("content")).startswith(_COMPACTION_SUMMARY)] + streamed
            source_file = updates

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
        "source_file": str(source_file),
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
