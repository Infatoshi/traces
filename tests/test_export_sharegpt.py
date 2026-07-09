from __future__ import annotations

import json

from traces.config import ExportConfig
from traces.export.sharegpt import export_sharegpt
from tests.conftest import make_conversation, make_message, write_jsonl


def test_two_turn_conversation_yields_two_pairs(tmp_path):
    in_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "x.jsonl", [
        make_conversation(messages=[
            make_message("user", "q1"), make_message("assistant", "a1"),
            make_message("user", "q2"), make_message("assistant", "a2"),
        ]),
    ])
    out_file = tmp_path / "out" / "sharegpt.jsonl"
    summary = export_sharegpt(in_dir, out_file, ExportConfig(variant="sharegpt"))
    rows = [json.loads(line) for line in out_file.read_text().strip().split("\n")]
    assert len(rows) == 2
    assert rows[0]["conversations"] == [
        {"from": "human", "value": "q1"},
        {"from": "gpt", "value": "a1"},
    ]
    assert rows[1]["conversations"] == [
        {"from": "human", "value": "q2"},
        {"from": "gpt", "value": "a2"},
    ]
    assert summary["pairs"] == 2


def test_trailing_user_without_assistant_dropped(tmp_path):
    in_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "x.jsonl", [
        make_conversation(messages=[
            make_message("user", "q1"), make_message("assistant", "a1"),
            make_message("user", "orphan"),
        ]),
    ])
    out_file = tmp_path / "out" / "sharegpt.jsonl"
    summary = export_sharegpt(in_dir, out_file, ExportConfig(variant="sharegpt"))
    rows = [json.loads(line) for line in out_file.read_text().strip().split("\n")]
    assert len(rows) == 1
    assert summary["dropped_trailing_user"] == 1


def test_system_prepended_to_first_pair_only(tmp_path):
    in_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "x.jsonl", [
        make_conversation(messages=[
            make_message("system", "be helpful"),
            make_message("user", "q1"), make_message("assistant", "a1"),
            make_message("user", "q2"), make_message("assistant", "a2"),
        ]),
    ])
    out_file = tmp_path / "out" / "sharegpt.jsonl"
    export_sharegpt(in_dir, out_file, ExportConfig(variant="sharegpt"))
    rows = [json.loads(line) for line in out_file.read_text().strip().split("\n")]
    # First pair has system prepended
    assert rows[0]["conversations"][0] == {"from": "system", "value": "be helpful"}
    assert rows[0]["conversations"][1] == {"from": "human", "value": "q1"}
    # Second pair has NO system
    assert all(c["from"] != "system" for c in rows[1]["conversations"])
