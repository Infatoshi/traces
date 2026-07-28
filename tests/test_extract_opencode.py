"""Tests for the OpenCode extractor (CLI JSON + Desktop Tauri .dat)."""
from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

from traces.extract.opencode import OpenCodeExtractor


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_extract_cli_basic_with_session_metadata(tmp_path):
    install = tmp_path / "opencode"
    session_id = "ses_abc"
    msg_id = "msg_1"

    # session metadata
    write_json(install / "storage" / "session" / "global" / f"{session_id}.json", {
        "title": "my session",
        "time": {"created": 1000, "updated": 2000},
        "projectID": "proj1",
        "directory": "/tmp/proj",
        "version": "1",
    })
    # a user message
    write_json(install / "storage" / "message" / session_id / "msg_1.json", {
        "id": msg_id, "role": "user",
        "time": {"created": 1000},
    })
    # user text part
    write_json(install / "storage" / "part" / msg_id / "prt_1.json", {
        "type": "text", "text": "hello",
    })
    # an assistant message with model metadata
    write_json(install / "storage" / "message" / session_id / "msg_2.json", {
        "id": "msg_2", "role": "assistant",
        "time": {"created": 2000},
        "modelID": "claude-sonnet-4-5", "providerID": "anthropic",
    })
    write_json(install / "storage" / "part" / "msg_2" / "prt_2.json", {
        "type": "text", "text": "hi there",
    })

    convs = list(OpenCodeExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "opencode-cli"
    assert conv["session_id"] == session_id
    assert conv["title"] == "my session"
    assert conv["project_id"] == "proj1"
    assert conv["directory"] == "/tmp/proj"
    assert conv["created_at"] == 1000
    assert conv["updated_at"] == 2000
    assert len(conv["messages"]) == 2
    assert conv["messages"][0] == {"role": "user", "content": "hello", "timestamp": 1000}
    asst = conv["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["content"] == "hi there"
    assert asst["model"] == "claude-sonnet-4-5"
    assert asst["provider"] == "anthropic"


def test_extract_cli_tool_call_and_result_attached(tmp_path):
    install = tmp_path / "opencode"
    session_id = "ses_t"
    msg_id = "msg_a"

    write_json(install / "storage" / "message" / session_id / "msg_a.json", {
        "id": msg_id, "role": "assistant", "time": {"created": 1},
    })
    # a 'tool' part with completed state -> tool_calls + tool_results
    write_json(install / "storage" / "part" / msg_id / "prt_1.json", {
        "type": "tool", "tool": "run", "callID": "call_1",
        "state": {"status": "completed", "input": {"cmd": "ls"}, "output": "file.txt"},
    })

    convs = list(OpenCodeExtractor().extract(install))
    asst = convs[0]["messages"][0]
    assert asst["role"] == "assistant"
    assert asst["content"] == ""
    assert asst["tool_calls"] == [{"id": "call_1", "name": "run", "input": {"cmd": "ls"}}]
    assert asst["tool_results"] == [{"tool_call_id": "call_1", "tool": "run",
                                     "output": "file.txt"}]


def test_missing_session_metadata_uses_structured_cwd_not_message_regex(tmp_path):
    install = tmp_path / "opencode"
    session_id = "ses_structured"
    msg_id = "msg_1"
    write_json(install / "storage" / "message" / session_id / f"{msg_id}.json", {
        "id": msg_id,
        "sessionID": session_id,
        "role": "assistant",
        "time": {"created": 1000, "completed": 2000},
        "path": {"cwd": "/structured/project", "root": "/structured/project"},
        "modelID": "model-a",
        "tokens": {
            "input": 10,
            "output": 5,
            "reasoning": 2,
            "cache": {"read": 3, "write": 1},
        },
    })
    write_json(install / "storage" / "part" / msg_id / "prt_1.json", {
        "id": "prt_1",
        "sessionID": session_id,
        "messageID": msg_id,
        "type": "text",
        "text": "Ignore this prose: cd /wrong/project and project-id=fake.",
    })

    conv = list(OpenCodeExtractor().extract(install))[0]

    assert conv["project_path"] == "/structured/project"
    assert conv.get("project_id") is None
    assert conv["token_usage"] == {
        "input": 10,
        "output": 5,
        "reasoning": 2,
        "cached_input": 3,
        "cache_write": 1,
    }


def test_extract_cli_empty_install(tmp_path):
    install = tmp_path / "opencode"
    (install / "storage" / "message").mkdir(parents=True)
    assert list(OpenCodeExtractor().extract(install)) == []


def test_extract_current_sqlite_tool_result_and_exit_metadata(tmp_path):
    install = tmp_path / "opencode"
    install.mkdir()
    database = install / "opencode.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, slug TEXT,
            directory TEXT, title TEXT, version TEXT, time_created INTEGER,
            time_updated INTEGER, model TEXT, tokens_input INTEGER,
            tokens_output INTEGER, tokens_reasoning INTEGER,
            tokens_cache_read INTEGER, tokens_cache_write INTEGER
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
            time_updated INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        );
    """)
    connection.execute(
        "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ses_sql", "proj", None, "slug", "/work/project", "title", "1",
            1000, 2000, "model-a", 10, 5, 2, 3, 1,
        ),
    )
    connection.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        ("msg_u", "ses_sql", 1000, 1000, json.dumps({"role": "user"})),
    )
    connection.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        (
            "msg_a", "ses_sql", 2000, 2000,
            json.dumps({
                "role": "assistant",
                "model": {"modelID": "model-a", "providerID": "provider-a"},
            }),
        ),
    )
    parts = [
        ("p1", "msg_u", 1000, {"type": "text", "text": "run tests"}),
        (
            "p2", "msg_a", 2000,
            {
                "type": "tool", "tool": "bash", "callID": "call-1",
                "state": {
                    "status": "completed",
                    "input": {"command": "pytest -q"},
                    "output": "passed",
                    "metadata": {"exit": 0},
                },
            },
        ),
        ("p3", "msg_a", 2001, {"type": "text", "text": "done"}),
        ("p4", "msg_a", 2002, {"type": "step-finish", "reason": "stop"}),
        (
            "p5", "msg_a", 2003,
            {"type": "patch", "hash": "a" * 40, "files": ["example.py"]},
        ),
    ]
    for part_id, message_id, timestamp, data in parts:
        connection.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            (part_id, message_id, "ses_sql", timestamp, timestamp, json.dumps(data)),
        )
    connection.commit()
    connection.close()

    conversations = list(OpenCodeExtractor().extract(install))

    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation["status"] == "completed"
    assert conversation["project_path"] == "/work/project"
    assert conversation["token_usage"] == {
        "input": 10,
        "output": 5,
        "reasoning": 2,
        "cached_input": 3,
        "cache_write": 1,
    }
    assistant_call = conversation["messages"][1]
    tool_result = conversation["messages"][2]["tool_result"]
    assert assistant_call["tool_calls"][0]["input"] == {
        "command": "pytest -q"
    }
    assert tool_result["metadata"] == {"exit": 0}
    assert tool_result["output"] == "passed"
    assert conversation["messages"][3]["content"] == "done"
    assert conversation["messages"][3]["diffs"] == [{
        "files": ["example.py"],
        "hash": "a" * 40,
    }]


def test_extract_desktop_tauri_dat(tmp_path):
    # Build a minimal Tauri .dat store with one conversation entry.
    install = tmp_path / "ai.opencode.app"
    install.mkdir(parents=True)
    dat = install / "store.dat"

    conv_value = {"messages": [{"role": "user", "content": "hi"}],
                  "session_id": "d1", "title": "desktop chat"}
    conv_value_bytes = json.dumps(conv_value).encode("utf-8")
    key_bytes = b"session:d1"

    blob = bytearray()
    blob += struct.pack("<I", len(key_bytes)) + key_bytes
    blob += struct.pack("<I", len(conv_value_bytes)) + conv_value_bytes

    dat.write_bytes(bytes(blob))

    convs = list(OpenCodeExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "opencode-desktop"
    assert conv["session_id"] == "d1"
    assert conv["title"] == "desktop chat"
    assert conv["store_key"] == "session:d1"
    assert conv["messages"] == [{"role": "user", "content": "hi"}]


def test_find_installations_discovers_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    (tmp_path / ".local" / "share" / "opencode").mkdir(parents=True)
    installs = OpenCodeExtractor().find_installations()
    assert any(p.name == "opencode" for p in installs)
