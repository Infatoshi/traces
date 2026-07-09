"""Synthetic trace factories reused across all test suites."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


# --- Secrets / PII canaries (the kinds the redactor must catch) ---
CANARY_OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD"
CANARY_GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
CANARY_EMAIL = "alex.morgan@example.com"
CANARY_HOME_PATH = "/Users/alexmorgan/projects/secret-client"
CANARY_PRIVATE_URL = "https://internal.company.local/api/v1/users"
CANARY_PHONE = "phone: +1-555-867-5309"
CANARY_BEARER = "Authorization: Bearer eyJhbGci.payload.signature"


def make_message(
    role: str = "user",
    content: str = "hello",
    **extra: Any,
) -> dict[str, Any]:
    msg = {"role": role, "content": content}
    msg.update(extra)
    return msg


def make_conversation(
    messages: list[dict[str, Any]] | None = None,
    source: str = "claude_code",
    **extra: Any,
) -> dict[str, Any]:
    if messages is None:
        messages = [
            make_message("user", "hi"),
            make_message("assistant", "hello"),
        ]
    conv: dict[str, Any] = {"messages": messages, "source": source}
    conv.update(extra)
    return conv


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


@pytest.fixture
def canary_conversation() -> dict[str, Any]:
    """One conversation seeded with every canary the redactor must catch."""
    return make_conversation(
        messages=[
            make_message("user", f"My key is {CANARY_OPENAI_KEY}"),
            make_message(
                "assistant",
                f"I'll use {CANARY_GITHUB_TOKEN} to clone {CANARY_PRIVATE_URL}",
                tool_use={"name": "run", "input": {"cmd": f"cat {CANARY_HOME_PATH}"}},
            ),
            make_message(
                "user",
                f"Email me at {CANARY_EMAIL}. {CANARY_PHONE}. {CANARY_BEARER}",
            ),
            make_message("assistant", "done"),
        ],
        source="claude_code",
        session_id="canary-001",
    )


@pytest.fixture
def canary_jsonl(tmp_path, canary_conversation) -> Path:
    return write_jsonl(tmp_path / "raw_extracted" / "claude_code.jsonl", [canary_conversation])
