"""Shared types for extractors."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol, TypedDict


class Message(TypedDict, total=False):
    id: str | None
    parent_id: str | None
    role: str            # "user" | "assistant" | "system" | "tool"
    content: Any
    timestamp: str | None
    model: str | None
    code_context: list[dict[str, Any]]
    suggested_diffs: list[dict[str, Any]]
    tool_use: dict[str, Any]
    tool_uses: list[dict[str, Any]]
    tool_call: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    tool_result: dict[str, Any]
    tool_results: list[dict[str, Any]]
    diff_histories: list[dict[str, Any]]
    diffs: list[dict[str, Any]]
    edits: list[dict[str, Any]]
    suggested_code_blocks: list[dict[str, Any]]
    phase: str
    reasoning: str
    finish_reason: str
    reasoning_effort: str
    tool_schema_hash: str


class Conversation(TypedDict, total=False):
    messages: list[Message]
    source: str
    name: str | None
    status: str | None
    version: str | int | None
    model: str | None
    reasoning_effort: str | None
    session_kind: str | None
    sandbox_profile: str | None
    session_id: str | None
    parent_session_id: str | None
    project_path: str | None
    project_name: str | None
    project_id: str | None
    project_hash: str | None
    workspace_id: str | None
    source_file: str | None
    created_at: int | str | None
    updated_at: int | str | None
    token_usage: dict[str, int | float]
    tool_call_count: int
    tool_result_count: int
    diff_count: int
    native_tool_call_count: int
    native_tool_result_count: int
    tools: list[dict[str, Any]]
    tool_snapshots: list[dict[str, Any]]
    tool_schema_hash: str | None
    tool_schema_quality: str


class Extractor(Protocol):
    """A source-specific extractor.

    Implementations discover local installations and yield normalized
    Conversation dicts. Source name is the canonical short key
    (e.g. "claude_code", "cursor").
    """

    source: str

    def find_installations(self) -> list[Path]: ...

    def extract(self, install_dir: Path) -> Iterator[Conversation]: ...
