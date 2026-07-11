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


def test_extracts_current_claude_shape_observed_in_local_sessions(tmp_path):
    install = tmp_path / ".claude"
    session = install / "projects" / "p" / "current.jsonl"
    write_session_jsonl(session, [
        {
            "type": "user",
            "uuid": "user-1",
            "parentUuid": None,
            "sessionId": "current",
            "cwd": "/work/project",
            "version": "2.1.0",
            "timestamp": "2026-07-10T10:00:00Z",
            "message": {"role": "user", "content": "run it"},
        },
        {
            "type": "assistant",
            "uuid": "assistant-record-1",
            "parentUuid": "user-1",
            "sessionId": "current",
            "cwd": "/work/project",
            "version": "2.1.0",
            "timestamp": "2026-07-10T10:00:01Z",
            "message": {
                "id": "assistant-message-1",
                "role": "assistant",
                "model": "claude-model",
                "content": [
                    {"type": "text", "text": "done"},
                    {"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {}},
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 2,
                },
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "uuid": "user-2",
            "parentUuid": "assistant-record-1",
            "sessionId": "current",
            "cwd": "/work/project",
            "version": "2.1.0",
            "timestamp": "2026-07-10T10:00:02Z",
            "message": {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": "ok",
                "is_error": False,
            }]},
        },
    ])

    conv = list(ClaudeCodeExtractor().extract(install))[0]

    assert conv["created_at"] == "2026-07-10T10:00:00Z"
    assert conv["updated_at"] == "2026-07-10T10:00:02Z"
    assert conv["version"] == "2.1.0"
    assert conv["token_usage"] == {
        "input": 10,
        "output": 5,
        "cached_input": 3,
        "cache_write": 2,
    }
    assert conv["messages"][1]["id"] == "assistant-message-1"
    assert conv["messages"][1]["parent_id"] == "user-1"
    assert conv["messages"][2]["tool_results"] == [{
        "tool_call_id": "tool-1",
        "status": "completed",
    }]


def test_skips_current_claude_auxiliary_sidecars(tmp_path):
    install = tmp_path / ".claude"
    project = install / "projects" / "p"
    write_session_jsonl(project / "attachment.jsonl", [
        {"type": "attachment", "attachment": {}},
        {"type": "queue-operation", "operation": "enqueue"},
        {"type": "user", "message": {"content": "synthetic request"}},
        {"type": "assistant", "message": {
            "model": "<synthetic>",
            "content": [{"type": "text", "text": "synthetic response"}],
        }},
        {"type": "last-prompt", "lastPrompt": "synthetic request"},
    ])
    write_session_jsonl(project / "title.jsonl", [
        {"type": "attachment", "attachment": {}},
        {"type": "queue-operation", "operation": "enqueue"},
        {"type": "user", "message": {"content": "title request"}},
        {"type": "assistant", "message": {
            "model": "claude-fable-5",
            "content": [{"type": "text", "text": "title"}],
        }},
        {"type": "last-prompt", "lastPrompt": "title request"},
        {"type": "ai-title", "title": "title"},
    ])

    assert list(ClaudeCodeExtractor().extract(install)) == []


def test_keeps_real_conversation_that_contains_ai_title_event(tmp_path):
    install = tmp_path / ".claude"
    session = install / "projects" / "p" / "real.jsonl"
    write_session_jsonl(session, [
        {"type": "user", "message": {"content": "real request"}},
        {"type": "assistant", "message": {
            "model": "claude-fable-5",
            "content": [{"type": "text", "text": "real response"}],
        }},
        {"type": "ai-title", "title": "generated title"},
    ])

    conversations = list(ClaudeCodeExtractor().extract(install))
    assert len(conversations) == 1
    assert conversations[0]["messages"][1]["content"] == "real response"


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
