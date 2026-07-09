"""Tests for the Trae extractor."""
from __future__ import annotations

import json
from pathlib import Path

from traces.extract.trae import TraeExtractor


def write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_extract_basic_user_assistant_turns(tmp_path):
    # Trae stores JSONL under <install>/projects/<proj>/*.jsonl
    install = tmp_path / ".trae"
    session = install / "projects" / "myproj" / "session.jsonl"
    write_jsonl(session, [
        {"type": "user", "message": "hello", "timestamp": "t1"},
        {"type": "assistant", "message": "hi there", "timestamp": "t2"},
    ])

    convs = list(TraeExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "trae"
    assert conv["source_file"] == str(session)
    assert len(conv["messages"]) == 2
    assert conv["messages"][0] == {"role": "user", "content": "hello", "timestamp": "t1"}
    assert conv["messages"][1] == {"role": "assistant", "content": "hi there", "timestamp": "t2"}


def test_extract_tool_use_and_diffs_attached(tmp_path):
    install = tmp_path / ".trae"
    session = install / "projects" / "p" / "s.jsonl"
    write_jsonl(session, [
        {"type": "user", "message": "do it"},
        {"type": "assistant", "message": "ok",
         "tool_use": {"tool": "edit", "input": {"path": "f.py"}},
         "diffs": [{"file": "f.py", "diff": "-a\n+b"}],
         "edits": [{"file": "g.py"}]},
    ])

    convs = list(TraeExtractor().extract(install))
    asst = convs[0]["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["content"] == "ok"
    assert asst["tool_use"] == {"tool": "edit", "input": {"path": "f.py"}}
    assert asst["diffs"] == [{"file": "f.py", "diff": "-a\n+b"}]
    assert asst["edits"] == [{"file": "g.py"}]


def test_extract_empty_install(tmp_path):
    install = tmp_path / ".trae"
    install.mkdir(parents=True)
    assert list(TraeExtractor().extract(install)) == []


def test_find_installations_discovers_trae(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".trae").mkdir()
    installs = TraeExtractor().find_installations()
    assert tmp_path / ".trae" in installs
