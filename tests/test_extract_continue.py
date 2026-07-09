"""Tests for the Continue extractor."""
from __future__ import annotations

import json
from pathlib import Path

from traces.extract.continue_ import ContinueExtractor


def write_session(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_extract_basic_user_assistant_turns(tmp_path):
    install = tmp_path / ".continue"
    session = install / "sessions" / "abc.json"
    write_session(session, {
        "sessionId": "abc",
        "title": "my chat",
        "workspaceDirectory": "/tmp/proj",
        "history": [
            {"message": {"role": "user", "content": "hello"}},
            {"message": {"role": "assistant",
                         "content": [{"type": "text", "text": "hi there"}]}},
        ],
    })

    convs = list(ContinueExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "continue"
    assert conv["session_id"] == "abc"
    assert conv["title"] == "my chat"
    assert conv["workspace"] == "/tmp/proj"
    assert len(conv["messages"]) == 2
    assert conv["messages"][0] == {"role": "user", "content": "hello"}
    assert conv["messages"][1]["role"] == "assistant"
    assert conv["messages"][1]["content"] == "hi there"


def test_extract_tool_calls_and_tool_results(tmp_path):
    install = tmp_path / ".continue"
    session = install / "sessions" / "t.json"
    write_session(session, {
        "sessionId": "t",
        "history": [
            {"message": {"role": "user", "content": "run it"}},
            {"message": {"role": "assistant",
                         "content": [{"type": "text", "text": "ok"}],
                         "toolCalls": [{"id": "1"}]},
             "toolCallStates": [
                 {"status": "done", "output": "result",
                  "tool": {"function": {"name": "run"}}},
             ]},
        ],
    })

    convs = list(ContinueExtractor().extract(install))
    asst = convs[0]["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["content"] == "ok"
    assert asst["tool_calls"] == [{"id": "1"}]
    assert asst["tool_results"][0]["tool"] == "run"
    assert asst["tool_results"][0]["output"] == "result"


def test_extract_empty_install(tmp_path):
    install = tmp_path / ".continue"
    install.mkdir()
    assert list(ContinueExtractor().extract(install)) == []


def test_find_installations_discovers_continue(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".continue").mkdir()
    installs = ContinueExtractor().find_installations()
    assert tmp_path / ".continue" in installs
