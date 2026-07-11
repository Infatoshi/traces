"""Claude Code extractor.

Vendored from 0xSero/ai-data-extraction/extract_claude_code.py (MIT).
Refactored to the Extractor protocol; discovery + parsing logic preserved.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Iterator

from traces.extract.base import Conversation


def find_claude_installations(home: Path | None = None) -> list[Path]:
    """Find all Claude Code installation directories."""
    home = home or Path.home()

    locations: list[Path] = []
    base_dirs = [
        home / "Library/Application Support",
        home / ".config",
        home / ".local/share",
    ]
    if platform.system() == "Windows" and home == Path.home():
        base_dirs.extend([
            Path(os.environ.get("APPDATA", home / "AppData/Roaming")),
            Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local")),
        ])

    claude_patterns = [
        "claude", "claude-code", "claude-local", "claude-m2", "claude-zai",
        ".claude", ".claude-code", ".claude-local", ".claude-m2", ".claude-zai",
    ]

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue
        for pattern in claude_patterns:
            candidate = base_dir / pattern
            if candidate.exists():
                locations.append(candidate)
    for pattern in claude_patterns:
        candidate = home / pattern
        if candidate.exists():
            locations.append(candidate)

    return list({p.resolve() for p in locations})


def extract_claude_project_conversations(project_dir: Path) -> list[Conversation]:
    """Extract conversations from a Claude project directory with full context."""
    conversations: list[Conversation] = []

    jsonl_files: list[Path] = []
    if (project_dir / "projects").exists():
        for proj in (project_dir / "projects").iterdir():
            if proj.is_dir():
                jsonl_files.extend(proj.glob("*.jsonl"))
    else:
        jsonl_files = list(project_dir.glob("*.jsonl"))
    jsonl_files = [f for f in jsonl_files if not f.name.startswith("agent-")]

    for jsonl_file in jsonl_files:
        try:
            messages: list[dict] = []
            session_id = jsonl_file.stem
            project_path: str | None = None
            project_name = jsonl_file.parent.name if jsonl_file.parent.name != "projects" else None
            timestamps: list[str] = []
            version: str | None = None
            record_types: set[str] = set()
            token_usage = {
                "input": 0,
                "output": 0,
                "cached_input": 0,
                "cache_write": 0,
            }

            with jsonl_file.open() as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg_type = obj.get("type")
                    if isinstance(msg_type, str):
                        record_types.add(msg_type)
                    timestamp = obj.get("timestamp")
                    if isinstance(timestamp, str):
                        timestamps.append(timestamp)
                    if isinstance(obj.get("version"), str):
                        version = obj["version"]
                    if msg_type == "user":
                        message = obj.get("message", {})
                        content = message.get("content", "")
                        wrapped_results: list[dict] = []
                        if isinstance(content, list):
                            text_parts: list[str] = []
                            for item in content:
                                if not isinstance(item, dict):
                                    continue
                                if item.get("type") == "text":
                                    text_parts.append(str(item.get("text", "")))
                                elif item.get("type") == "tool_result":
                                    wrapped_results.append({
                                        "tool_call_id": item.get("tool_use_id"),
                                        "status": "error" if item.get("is_error") else "completed",
                                    })
                            content = "\n".join(text_parts)
                        if content:
                            msg: dict = {"role": "user", "content": content,
                                         "timestamp": obj.get("timestamp")}
                            if obj.get("uuid"):
                                msg["id"] = obj["uuid"]
                            if obj.get("parentUuid"):
                                msg["parent_id"] = obj["parentUuid"]
                            if "toolUse" in obj:
                                msg["tool_use"] = obj["toolUse"]
                            messages.append(msg)
                        if wrapped_results:
                            messages.append({
                                "role": "tool",
                                "content": "",
                                "timestamp": obj.get("timestamp"),
                                "tool_results": wrapped_results,
                            })
                        if "cwd" in obj:
                            project_path = obj["cwd"]

                    elif msg_type == "assistant":
                        message = obj.get("message", {})
                        content = message.get("content", [])
                        text_parts: list[str] = []
                        tool_uses: list[dict] = []
                        if isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict):
                                    if item.get("type") == "text":
                                        text_parts.append(item.get("text", ""))
                                    elif item.get("type") == "tool_use":
                                        tool_uses.append(item)
                        elif isinstance(content, str):
                            text_parts.append(content)
                        full_text = "\n".join(text_parts)
                        if full_text or tool_uses:
                            msg = {"role": "assistant", "content": full_text,
                                   "model": message.get("model"),
                                   "timestamp": obj.get("timestamp")}
                            if message.get("id") or obj.get("uuid"):
                                msg["id"] = message.get("id") or obj.get("uuid")
                            if obj.get("parentUuid"):
                                msg["parent_id"] = obj["parentUuid"]
                            if message.get("stop_reason"):
                                msg["stop_reason"] = message["stop_reason"]
                            usage = message.get("usage")
                            if isinstance(usage, dict):
                                for source_key, target_key in (
                                    ("input_tokens", "input"),
                                    ("output_tokens", "output"),
                                    ("cache_read_input_tokens", "cached_input"),
                                    ("cache_creation_input_tokens", "cache_write"),
                                ):
                                    value = usage.get(source_key)
                                    if isinstance(value, (int, float)):
                                        token_usage[target_key] += value
                            if tool_uses:
                                msg["tool_uses"] = tool_uses
                            messages.append(msg)

                    elif msg_type == "tool_result":
                        tool_result = obj.get("toolResult", {})
                        if tool_result and messages:
                            messages[-1].setdefault("tool_results", []).append(tool_result)

            assistant_models = {
                message.get("model")
                for message in messages
                if message.get("role") == "assistant" and message.get("model")
            }
            is_sidecar = (
                len(messages) == 2
                and {"attachment", "queue-operation", "last-prompt"}
                <= record_types
            )
            is_auxiliary = assistant_models == {"<synthetic>"} or is_sidecar
            if messages and not is_auxiliary:
                conversation: Conversation = {
                    "messages": messages,
                    "source": "claude_code",
                    "session_id": session_id,
                    "project_path": project_path,
                    "project_name": project_name,
                    "source_file": str(jsonl_file),
                    "installation": str(project_dir),
                }
                if timestamps:
                    conversation["created_at"] = min(timestamps)
                    conversation["updated_at"] = max(timestamps)
                if version:
                    conversation["version"] = version
                nonzero_usage = {
                    key: value for key, value in token_usage.items() if value
                }
                if nonzero_usage:
                    conversation["token_usage"] = nonzero_usage
                conversations.append(conversation)
        except Exception as e:  # noqa: BLE001 - upstream swallows per-file errors
            print(f"Error processing {jsonl_file}: {e}")
            continue

    return conversations


class ClaudeCodeExtractor:
    source = "claude_code"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def find_installations(self) -> list[Path]:
        return find_claude_installations(self.root)

    def extract(self, install_dir: Path) -> Iterator[Conversation]:
        for conv in extract_claude_project_conversations(install_dir):
            yield conv
