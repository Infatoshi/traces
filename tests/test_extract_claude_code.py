"""Tests for the Claude Code extractor."""
from __future__ import annotations

import json
from pathlib import Path

from traces.extract.claude_code import ClaudeCodeExtractor


def write_session_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_extract_basic_user_assistant_turns(tmp_path):
    # Arrange: fake a .claude/projects/myproj/<session>.jsonl
    install = tmp_path / ".claude"
    proj = install / "projects" / "myproj"
    session = proj / "abc123.jsonl"
    write_session_jsonl(session, [
        {"type": "user", "timestamp": "t1", "cwd": "/tmp/proj",
         "message": {"content": "hello"}},
        {"type": "assistant", "timestamp": "t2",
         "message": {"content": [{"type": "text", "text": "hi there"}],
                     "model": "claude-sonnet-4-5"}},
    ])

    ext = ClaudeCodeExtractor()
    convs = list(ext.extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "claude_code"
    assert conv["session_id"] == "abc123"
    assert conv["project_path"] == "/tmp/proj"
    assert len(conv["messages"]) == 2
    assert conv["messages"][0] == {"role": "user", "content": "hello", "timestamp": "t1"}
    assert conv["messages"][1]["role"] == "assistant"
    assert conv["messages"][1]["content"] == "hi there"
    assert conv["messages"][1]["model"] == "claude-sonnet-4-5"


def test_extract_tool_use_and_tool_result_attached(tmp_path):
    install = tmp_path / ".claude"
    proj = install / "projects" / "p"
    session = proj / "s.jsonl"
    write_session_jsonl(session, [
        {"type": "user", "message": {"content": "do the thing"}},
        {"type": "assistant",
         "message": {"content": [
             {"type": "text", "text": "ok"},
             {"type": "tool_use", "name": "edit", "input": {"path": "f.py"}},
         ], "model": "m"}},
        {"type": "tool_result", "toolResult": {"type": "text", "text": "done"}},
    ])

    convs = list(ClaudeCodeExtractor().extract(install))
    asst = convs[0]["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["content"] == "ok"
    assert asst["tool_uses"][0]["name"] == "edit"
    assert asst["tool_results"][0]["text"] == "done"


def test_extract_skips_empty_session(tmp_path):
    install = tmp_path / ".claude"
    proj = install / "projects" / "p"
    write_session_jsonl(proj / "empty.jsonl", [])
    convs = list(ClaudeCodeExtractor().extract(install))
    assert convs == []


def test_find_installations_discovers_dotclaude(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()

    ext = ClaudeCodeExtractor()
    installs = ext.find_installations()
    assert tmp_path / ".claude" in installs
