"""Tests for the Codex extractor."""
from __future__ import annotations

import json
from pathlib import Path

from traces.extract.codex import CodexExtractor


def write_rollout(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_extract_basic_user_assistant_turns(tmp_path):
    install = tmp_path / ".codex"
    session = install / "sessions" / "2024" / "01" / "01" / "rollout-abc.jsonl"
    write_rollout(session, [
        {"type": "session_meta", "payload": {"id": "abc", "cwd": "/tmp/proj",
                                              "timestamp": "2024-01-01T00:00:00Z"}},
        {"type": "event_msg", "timestamp": "t1",
         "payload": {"type": "user_message", "message": "hello"}},
        {"type": "event_msg", "timestamp": "t2",
         "payload": {"type": "agent_message", "message": "hi there",
                     "model": "gpt-5-codex"}},
    ])

    convs = list(CodexExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "codex"
    assert conv["session_id"] == "abc"
    assert conv["cwd"] == "/tmp/proj"
    assert conv["timestamp"] == "2024-01-01T00:00:00Z"
    assert len(conv["messages"]) == 2
    assert conv["messages"][0] == {"role": "user", "content": "hello", "timestamp": "t1"}
    assert conv["messages"][1]["role"] == "assistant"
    assert conv["messages"][1]["content"] == "hi there"
    assert conv["messages"][1]["model"] == "gpt-5-codex"


def test_extract_tool_use_and_tool_results_collected(tmp_path):
    install = tmp_path / ".codex"
    session = install / "sessions" / "rollout-r.jsonl"
    write_rollout(session, [
        {"type": "session_meta", "payload": {"id": "r"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "do it"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "ok"}},
        {"type": "event_msg", "payload": {"type": "tool_use", "tool": "shell",
                                          "input": {"cmd": "ls"}}},
        {"type": "event_msg", "payload": {"type": "tool_result", "tool": "shell",
                                          "output": "file.txt"}},
        {"type": "event_msg", "payload": {"type": "diff", "file": "f.py",
                                          "diff": "-old\n+new"}},
    ])

    convs = list(CodexExtractor().extract(install))
    conv = convs[0]
    # Codex collects tool_use/tool_result/diff into a top-level tool_results list
    assert len(conv["tool_results"]) == 3
    assert conv["tool_results"][0]["type"] == "tool_use"
    assert conv["tool_results"][0]["tool"] == "shell"
    assert conv["tool_results"][1]["type"] == "tool_result"
    assert conv["tool_results"][2]["type"] == "diff"
    assert conv["tool_results"][2]["file"] == "f.py"


def test_extract_empty_install(tmp_path):
    install = tmp_path / ".codex"
    install.mkdir(parents=True)
    assert list(CodexExtractor().extract(install)) == []


def test_find_installations_discovers_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".codex").mkdir()
    installs = CodexExtractor().find_installations()
    assert tmp_path / ".codex" in installs
