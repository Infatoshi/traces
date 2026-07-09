"""Tests for the Grok Build extractor."""
from __future__ import annotations

import json
from pathlib import Path

from traces.extract.grok_build import GrokBuildExtractor, extract_grok_session


def _write_session(
    root: Path,
    cwd_encoded: str,
    session_id: str,
    lines: list[dict],
    summary: dict | None = None,
) -> Path:
    sess = root / "sessions" / cwd_encoded / session_id
    sess.mkdir(parents=True)
    with (sess / "chat_history.jsonl").open("w") as f:
        for obj in lines:
            f.write(json.dumps(obj) + "\n")
    if summary is not None:
        (sess / "summary.json").write_text(json.dumps(summary))
    return sess


def test_extract_basic_user_assistant(tmp_path):
    home = tmp_path / ".grok"
    _write_session(
        home,
        "%2Ftmp%2Fproj",
        "sess-1",
        [
            {"type": "system", "content": "You are Grok."},
            {"type": "user", "content": [{"type": "text", "text": "hello"}]},
            {
                "type": "assistant",
                "content": "hi there",
                "model_id": "grok-4.5",
            },
        ],
        summary={
            "info": {"id": "sess-1", "cwd": "/tmp/proj"},
            "generated_title": "Hello",
            "current_model_id": "grok-4.5",
            "session_kind": None,
        },
    )

    convs = list(GrokBuildExtractor(tmp_path).extract(home))
    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "grok_build"
    assert conv["session_id"] == "sess-1"
    assert conv["project_path"] == "/tmp/proj"
    assert conv["name"] == "Hello"
    roles = [m["role"] for m in conv["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert conv["messages"][1]["content"] == "hello"
    assert conv["messages"][2]["content"] == "hi there"
    assert conv["messages"][2]["model"] == "grok-4.5"


def test_extract_tool_calls_and_results(tmp_path):
    home = tmp_path / ".grok"
    _write_session(
        home,
        "cwd",
        "s2",
        [
            {"type": "user", "content": "run ls"},
            {
                "type": "assistant",
                "content": "ok",
                "model_id": "grok-4.5",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "run_terminal_command",
                        "arguments": json.dumps({"command": "ls"}),
                    }
                ],
            },
            {
                "type": "tool_result",
                "tool_call_id": "call-1",
                "content": "file.txt",
            },
        ],
    )

    conv = extract_grok_session(home / "sessions" / "cwd" / "s2")
    assert conv is not None
    assert conv["messages"][1]["role"] == "assistant"
    assert conv["messages"][1]["tool_uses"][0]["name"] == "run_terminal_command"
    assert conv["messages"][1]["tool_uses"][0]["input"] == {"command": "ls"}
    assert conv["messages"][2]["role"] == "tool"
    assert conv["messages"][2]["content"] == "file.txt"


def test_extract_reasoning_summary(tmp_path):
    home = tmp_path / ".grok"
    _write_session(
        home,
        "cwd",
        "s3",
        [
            {"type": "user", "content": "think"},
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "planning steps"}],
                "encrypted_content": "opaque",
                "model_id": "grok-4.5",
            },
            {"type": "assistant", "content": "done", "model_id": "grok-4.5"},
        ],
    )
    conv = extract_grok_session(home / "sessions" / "cwd" / "s3")
    assert conv is not None
    assert conv["messages"][1]["role"] == "assistant"
    assert "planning steps" in conv["messages"][1]["content"]
    assert "<reasoning>" in conv["messages"][1]["content"]


def test_extract_empty_install(tmp_path):
    home = tmp_path / ".grok"
    home.mkdir()
    (home / "sessions").mkdir()
    assert list(GrokBuildExtractor().extract(home)) == []


def test_find_installations_discovers_grok(tmp_path):
    (tmp_path / ".grok" / "sessions").mkdir(parents=True)
    installs = GrokBuildExtractor(tmp_path).find_installations()
    assert any(p.name == ".grok" for p in installs)


def test_cwd_from_encoded_parent_when_summary_missing(tmp_path):
    home = tmp_path / ".grok"
    _write_session(
        home,
        "%2FUsers%2Finfatoshi%2Fdev",
        "s4",
        [{"type": "user", "content": "x"}, {"type": "assistant", "content": "y"}],
    )
    conv = extract_grok_session(
        home / "sessions" / "%2FUsers%2Finfatoshi%2Fdev" / "s4"
    )
    assert conv is not None
    assert conv["project_path"] == "/Users/infatoshi/dev"
