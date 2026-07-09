"""Tests for the Windsurf extractor."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from traces.extract.windsurf import WindsurfExtractor


def make_vscdb(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS ItemTable (key TEXT, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS cursorDiskKV (key TEXT, value TEXT)")
    conn.commit()
    conn.close()


def put_item(db_path: Path, table: str, key: str, value: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"INSERT INTO {table} (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def test_extract_chat_mode_basic(tmp_path):
    install = tmp_path / "Windsurf"
    ws_db = install / "User" / "workspaceStorage" / "WS1" / "state.vscdb"
    make_vscdb(ws_db)
    chat = {
        "tabs": [
            {"chatTitle": "my chat", "tabId": "t1", "bubbles": [
                {"type": "user", "rawText": "hello"},
                {"type": "assistant", "rawText": "hi there"},
            ]},
        ]
    }
    put_item(ws_db, "ItemTable", "workbench.panel.aichat.view.aichat.chatdata", json.dumps(chat))

    convs = list(WindsurfExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "windsurf-chat"
    assert conv["chat_title"] == "my chat"
    assert conv["tab_id"] == "t1"
    assert conv["workspace_id"] == "WS1"
    assert len(conv["messages"]) == 2
    assert conv["messages"][0] == {"role": "user", "content": "hello"}
    assert conv["messages"][1] == {"role": "assistant", "content": "hi there"}


def test_extract_agent_with_suggested_diffs(tmp_path):
    # agent/flow conversations live in the global DB's cursorDiskKV, with
    # suggestedCodeBlocks / diffHistories attached to AI bubbles.
    install = tmp_path / "Windsurf"
    global_db = install / "User" / "globalStorage" / "state.vscdb"
    make_vscdb(global_db)
    agent = {
        "name": "refactor task",
        "status": "done",
        "createdAt": 1000,
        "lastUpdatedAt": 2000,
        "conversation": [
            {"type": 1, "text": "do it"},
            {"type": 2, "text": "done",
             "suggestedCodeBlocks": [{"file": "f.py", "diff": "-a\n+b"}]},
        ],
    }
    put_item(global_db, "cursorDiskKV", "agentData:a1", json.dumps(agent))

    convs = list(WindsurfExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "windsurf-agent"
    assert conv["name"] == "refactor task"
    assert conv["status"] == "done"
    asst = conv["messages"][1]
    assert asst["suggested_code_blocks"] == [{"file": "f.py", "diff": "-a\n+b"}]


def test_extract_empty_install(tmp_path):
    install = tmp_path / "Windsurf"
    install.mkdir(parents=True)
    assert list(WindsurfExtractor().extract(install)) == []


def test_find_installations_discovers_windsurf(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Library" / "Application Support" / "Windsurf").mkdir(parents=True)
    installs = WindsurfExtractor().find_installations()
    assert any(p.name == "Windsurf" for p in installs)
