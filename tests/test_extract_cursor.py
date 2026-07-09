"""Tests for the Cursor extractor.

Cursor stores data in VSCode-style SQLite ``state.vscdb`` files. This test
suite builds synthetic DBs with the right schema for two of the four
upstream formats:
  - v0.43-v1.x workspace composer (``composer.composerData`` in workspace
    ItemTable, bubbles with ``type`` 1=user / 2=AI)
  - v2.0+ global composer (``composerData:*`` rows in the global DB's
    ``cursorDiskKV`` table)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from traces.extract.cursor import CursorExtractor


def make_vscdb(db_path: Path) -> None:
    """Create a VSCode-style state.vscdb with the ItemTable + cursorDiskKV tables."""
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


def test_extract_workspace_composer_basic(tmp_path):
    # v0.43-v1.x: composer.composerData in workspace ItemTable
    install = tmp_path / "Cursor"
    ws_db = install / "User" / "workspaceStorage" / "WS1" / "state.vscdb"
    make_vscdb(ws_db)
    composer = {
        "allComposers": [
            {
                "composerId": "comp1",
                "name": "my chat",
                "modelConfig": {"modelName": "gpt-4"},
                "conversation": [
                    {"type": 1, "text": "hello"},
                    {"type": 2, "text": "hi there", "codeBlocks": ["x = 1"]},
                ],
            }
        ]
    }
    put_item(ws_db, "ItemTable", "composer.composerData", json.dumps(composer))

    convs = list(CursorExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "cursor-workspace-composer"
    assert conv["composer_id"] == "comp1"
    assert conv["name"] == "my chat"
    assert conv["workspace_id"] == "WS1"
    assert conv["model"] == "gpt-4"
    assert len(conv["messages"]) == 2
    # Only assistant messages get model stamped from modelConfig; user does not
    assert conv["messages"][0] == {"role": "user", "content": "hello"}
    asst = conv["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["content"] == "hi there"
    assert asst["model"] == "gpt-4"
    assert asst["code_blocks"] == ["x = 1"]


def test_extract_global_composer_v2_inline_diff_histories(tmp_path):
    # v2.0+ inline storage: composerData:<id> in global cursorDiskKV, AI bubble
    # carries diffHistories (Cursor's attachment-style field for inline storage).
    install = tmp_path / "Cursor"
    global_db = install / "User" / "globalStorage" / "state.vscdb"
    make_vscdb(global_db)
    composer = {
        "composerId": "gcomp1",
        "name": "global chat",
        "modelConfig": {"modelName": "claude-3.5-sonnet"},
        "conversation": [
            {"type": 1, "text": "refactor this"},
            {"type": 2, "text": "done",
             "diffHistories": [{"file": "f.py", "diff": "-old\n+new"}]},
        ],
    }
    put_item(global_db, "cursorDiskKV", "composerData:gcomp1", json.dumps(composer))

    convs = list(CursorExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "cursor-global-composer"
    assert conv["composer_id"] == "gcomp1"
    assert conv["storage_type"] == "inline"
    assert conv["model"] == "claude-3.5-sonnet"
    asst = conv["messages"][1]
    assert asst["diff_histories"] == [{"file": "f.py", "diff": "-old\n+new"}]
    assert conv["has_diffs"] is True


def test_extract_global_composer_v2_separate_storage_tool_results(tmp_path):
    # v2.0+ SEPARATE storage: composerData:<id> has no inline conversation, so
    # upstream falls back to extract_bubbles_for_composer() reading
    # bubbleId:<id>:<bubble> rows. This path (unlike the inline path) DOES
    # attach toolResults to AI bubbles — exercised here.
    install = tmp_path / "Cursor"
    global_db = install / "User" / "globalStorage" / "state.vscdb"
    make_vscdb(global_db)
    # composer row with empty conversation -> triggers separate-storage lookup
    composer = {"composerId": "sep1", "name": "sep chat",
                "modelConfig": {"modelName": "gpt-4o"},
                "conversation": []}
    put_item(global_db, "cursorDiskKV", "composerData:sep1", json.dumps(composer))
    # two bubbles under separate storage
    put_item(global_db, "cursorDiskKV", "bubbleId:sep1:u1",
             json.dumps({"type": 1, "text": "do it"}))
    put_item(global_db, "cursorDiskKV", "bubbleId:sep1:a1",
             json.dumps({"type": 2, "text": "done",
                         "toolResults": [{"tool": "edit", "output": "ok"}]}))

    convs = list(CursorExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "cursor-global-composer"
    assert conv["storage_type"] == "separate"
    asst = conv["messages"][1]
    assert asst["role"] == "assistant"
    assert asst["tool_results"] == [{"tool": "edit", "output": "ok"}]


def test_extract_empty_install(tmp_path):
    install = tmp_path / "Cursor"
    install.mkdir(parents=True)
    assert list(CursorExtractor().extract(install)) == []


def test_find_installations_discovers_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Library" / "Application Support" / "Cursor").mkdir(parents=True)
    installs = CursorExtractor().find_installations()
    assert any(p.name == "Cursor" for p in installs)
