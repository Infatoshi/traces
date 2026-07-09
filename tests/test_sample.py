from __future__ import annotations

import json
from pathlib import Path

from traces.sample import sample_conversations
from tests.conftest import make_conversation, make_message, write_jsonl


def test_sample_reproducible_with_seed(tmp_path: Path):
    in_dir = tmp_path / "export"
    records = [
        make_conversation(
            messages=[make_message("user", str(i)), make_message("assistant", str(i))],
            source="claude_code",
        )
        for i in range(20)
    ]
    write_jsonl(in_dir / "claude_code.jsonl", records)

    first = sample_conversations(in_dir, n=5, seed=42)
    second = sample_conversations(in_dir, n=5, seed=42)

    assert len(first) == 5
    assert [r["messages"][0]["content"] for r in first] == \
        [r["messages"][0]["content"] for r in second]


def test_sample_writes_out_file(tmp_path: Path):
    in_dir = tmp_path / "export"
    write_jsonl(in_dir / "claude_code.jsonl", [
        make_conversation(source="claude_code") for _ in range(10)
    ])
    out_dir = tmp_path / "sample_out"

    rows = sample_conversations(in_dir, n=3, seed=1, out_dir=out_dir)

    assert len(rows) == 3
    out_file = out_dir / "sample.jsonl"
    assert out_file.exists()
    written = [json.loads(line) for line in out_file.read_text().strip().split("\n")]
    assert len(written) == 3


def test_sample_n_larger_than_corpus(tmp_path: Path):
    in_dir = tmp_path / "export"
    write_jsonl(in_dir / "claude_code.jsonl", [
        make_conversation(source="claude_code") for _ in range(2)
    ])
    rows = sample_conversations(in_dir, n=100, seed=0)
    assert len(rows) == 2
