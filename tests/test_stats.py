from __future__ import annotations

from pathlib import Path

from traces.stats import corpus_stats
from tests.conftest import make_conversation, make_message, write_jsonl


def test_stats_happy_path(tmp_path: Path):
    in_dir = tmp_path / "export"
    write_jsonl(in_dir / "claude_code.jsonl", [
        make_conversation(
            messages=[
                make_message("user", "hi"),
                make_message("assistant", "hello there"),
            ],
            source="claude_code",
        ),
        make_conversation(
            messages=[
                make_message("user", "again"),
                make_message("assistant", "yes"),
                make_message("user", "ok"),
                make_message("assistant", "done"),
            ],
            source="claude_code",
        ),
    ])
    write_jsonl(in_dir / "codex.jsonl", [
        make_conversation(
            messages=[make_message("user", "x"), make_message("assistant", "y")],
            source="codex",
        ),
    ])

    report = corpus_stats(in_dir)

    assert report["conversations_by_source"] == {"claude_code": 2, "codex": 1}
    assert report["total_conversations"] == 3
    assert report["turn_count_min"] == 2
    assert report["turn_count_max"] == 4
    assert report["turn_count_mean"] == (2 + 4 + 2) / 3
    assert report["total_assistant_chars"] == len("hello there") + len("yes") + len("done") + len("y")


def test_stats_empty_dir(tmp_path: Path):
    in_dir = tmp_path / "empty"
    in_dir.mkdir()
    report = corpus_stats(in_dir)
    assert report["total_conversations"] == 0
    assert report["turn_count_min"] == 0
    assert report["turn_count_mean"] == 0
    assert report["total_assistant_chars"] == 0
