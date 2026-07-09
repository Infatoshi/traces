"""Tests for the Gemini CLI extractor."""
from __future__ import annotations

import json
from pathlib import Path

from traces.extract.gemini import GeminiExtractor


def write_session(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_extract_basic_user_assistant_turns(tmp_path):
    # Gemini stores sessions under <install>/tmp/<hash>/chats/session-*.json
    install = tmp_path / ".gemini"
    session = install / "tmp" / "abc123" / "chats" / "session-001.json"
    write_session(session, {
        "sessionId": "001",
        "projectHash": "abc123",
        "startTime": "2024-01-01T00:00:00Z",
        "lastUpdated": "2024-01-01T00:01:00Z",
        "messages": [
            {"type": "user", "content": "hello", "timestamp": "t1"},
            {"type": "gemini", "content": "hi there", "timestamp": "t2",
             "model": "gemini-2.5-pro"},
        ],
    })

    convs = list(GeminiExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "gemini-cli"
    assert conv["session_id"] == "001"
    assert conv["project_hash"] == "abc123"
    assert conv["start_time"] == "2024-01-01T00:00:00Z"
    assert len(conv["messages"]) == 2
    assert conv["messages"][0] == {"role": "user", "content": "hello", "timestamp": "t1"}
    assert conv["messages"][1]["role"] == "assistant"
    assert conv["messages"][1]["content"] == "hi there"
    assert conv["messages"][1]["model"] == "gemini-2.5-pro"


def test_extract_attaches_thoughts_and_tokens(tmp_path):
    # Gemini's "thoughts" (reasoning) is the attachment-style field; tokens too.
    install = tmp_path / ".gemini"
    session = install / "tmp" / "h" / "chats" / "session-x.json"
    write_session(session, {
        "sessionId": "x",
        "messages": [
            {"type": "user", "content": "explain"},
            {"type": "gemini", "content": "answer",
             "thoughts": [{"text": "reasoning step"}],
             "tokens": {"input": 10, "output": 5}},
        ],
    })

    convs = list(GeminiExtractor().extract(install))
    asst = convs[0]["messages"][1]
    assert asst["thoughts"] == [{"text": "reasoning step"}]
    assert asst["tokens"] == {"input": 10, "output": 5}


def test_extract_empty_install(tmp_path):
    install = tmp_path / ".gemini"
    install.mkdir(parents=True)
    assert list(GeminiExtractor().extract(install)) == []


def test_find_installations_discovers_gemini(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".gemini").mkdir()
    installs = GeminiExtractor().find_installations()
    assert tmp_path / ".gemini" in installs
