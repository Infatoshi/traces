from __future__ import annotations

import json

from traces.config import ExportConfig
from traces.export.messages import export_messages
from tests.conftest import make_conversation, make_message, write_jsonl


def test_export_one_conversation(tmp_path):
    in_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "claude_code.jsonl", [
        make_conversation(messages=[make_message("user", "hi"), make_message("assistant", "hello")]),
    ])
    out_file = tmp_path / "out" / "messages.jsonl"

    summary = export_messages(in_dir, out_file, ExportConfig(variant="messages"))

    assert out_file.exists()
    rows = [json.loads(line) for line in out_file.read_text().strip().split("\n")]
    assert len(rows) == 1
    assert rows[0]["messages"][0] == {"role": "user", "content": "hi"}
    assert rows[0]["messages"][1] == {"role": "assistant", "content": "hello"}
    assert summary["rows"] == 1


def test_drops_conversation_with_no_assistant(tmp_path):
    in_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "x.jsonl", [
        make_conversation(messages=[make_message("user", "orphan")]),
    ])
    out_file = tmp_path / "out" / "messages.jsonl"
    summary = export_messages(in_dir, out_file, ExportConfig(variant="messages"))
    assert summary["rows"] == 0
    assert summary["dropped_no_assistant"] == 1


def test_min_turns_filter(tmp_path):
    in_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "x.jsonl", [
        make_conversation(messages=[make_message("user", "a"), make_message("assistant", "b")]),
        make_conversation(messages=[make_message("user", "a"), make_message("assistant", "b"),
                                    make_message("user", "c"), make_message("assistant", "d")]),
    ])
    out_file = tmp_path / "out" / "messages.jsonl"
    summary = export_messages(in_dir, out_file, ExportConfig(variant="messages", min_turns=4))
    assert summary["rows"] == 1


def test_rich_fields_flattened(tmp_path):
    in_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "x.jsonl", [
        make_conversation(messages=[
            make_message("user", "do it"),
            make_message("assistant", "ok", tool_use={"name": "edit", "input": {"path": "f"}}),
        ]),
    ])
    out_file = tmp_path / "out" / "messages.jsonl"
    export_messages(in_dir, out_file, ExportConfig(variant="messages"))
    rows = [json.loads(line) for line in out_file.read_text().strip().split("\n")]
    content = rows[0]["messages"][1]["content"]
    assert '<tool_use name="edit">' in content
