from __future__ import annotations

import hashlib
import json
from pathlib import Path

from traces import __version__
from traces.manifest import file_hashes, write_manifest


def _seed_export(out_root: Path, messages_rows: int, sharegpt_rows: int) -> None:
    export_dir = out_root / "export"
    export_dir.mkdir(parents=True, exist_ok=True)
    with (export_dir / "messages.jsonl").open("w") as f:
        for _ in range(messages_rows):
            f.write(json.dumps({"messages": [{"role": "user", "content": "hi"}]}) + "\n")
    with (export_dir / "sharegpt.jsonl").open("w") as f:
        for _ in range(sharegpt_rows):
            f.write(json.dumps({"conversations": [{"from": "human", "value": "hi"}]}) + "\n")


def test_file_hashes_sha256_bytes_rows(tmp_path):
    p = tmp_path / "f.jsonl"
    payload = b'{"a":1}\n{"b":2}\n'
    p.write_bytes(payload)
    h = file_hashes(p)
    assert h["sha256"] == hashlib.sha256(payload).hexdigest()
    assert h["bytes"] == len(payload)
    assert h["rows"] == 2


def test_write_manifest_roundtrip(tmp_path):
    out_root = tmp_path / "output"
    _seed_export(out_root, messages_rows=3, sharegpt_rows=2)

    # Two-source fixture: catches the old bug where every source's
    # `exported_messages` was set to the GLOBAL row count regardless of source.
    extract_counts = {"claude_code": 3, "cursor": 0}
    redact_report = {
        "counts": {"paths": 1},
        "per_source": {"claude_code": {"paths": 1}, "cursor": {}},
        "layers": {},
        "config": {"allow_public_urls": False},
    }
    export_summaries = {
        "messages": {"rows": 3, "dropped_no_assistant": 1, "dropped_filter": 0, "dropped_dedup": 0},
        "sharegpt": {"pairs": 2, "dropped_no_assistant": 0, "dropped_trailing_user": 1,
                     "dropped_filter": 0, "dropped_dedup": 0},
    }
    config_snapshot = {"variant": "both", "allow_public_urls": False}

    path = write_manifest(out_root, extract_counts, redact_report, export_summaries, config_snapshot)

    assert path == out_root / "MANIFEST.json"
    assert path.exists()
    manifest = json.loads(path.read_text())

    # Top-level provenance fields.
    assert manifest["tool_version"] == __version__
    assert manifest["metadata_schema_version"] == "1.0"
    assert "created_at" in manifest and manifest["created_at"]
    assert manifest["config"] == config_snapshot

    # Per-source block: raw + redaction counts only. The buggy per-source
    # `exported_messages` field must be ABSENT from every source.
    sources = manifest["sources"]
    assert set(sources.keys()) == {"claude_code", "cursor"}
    for name, s in sources.items():
        assert set(s.keys()) == {"raw_records", "redaction_counts"}, (
            f"source {name!r} has unexpected keys {set(s.keys())}; per-source "
            "export attribution was removed (export counts are global)"
        )
        assert "exported_messages" not in s
        assert "exported_sharegpt_pairs" not in s
    assert sources["claude_code"]["raw_records"] == 3
    assert sources["claude_code"]["redaction_counts"] == {"paths": 1}
    assert sources["cursor"]["raw_records"] == 0
    assert sources["cursor"]["redaction_counts"] == {}

    # Global export_summary block carries the counts that used to be (wrongly)
    # stamped onto each source.
    assert manifest["export_summary"] == {
        "messages_rows": 3,
        "messages_dropped_no_assistant": 1,
        "sharegpt_pairs": 2,
        "sharegpt_dropped_trailing_user": 1,
    }

    # Files block hashes both export variants and matches their byte/row counts.
    files = manifest["files"]
    assert set(files.keys()) == {"export/messages.jsonl", "export/sharegpt.jsonl"}
    assert files["export/messages.jsonl"]["rows"] == 3
    assert files["export/sharegpt.jsonl"]["rows"] == 2
    assert files["export/messages.jsonl"]["sha256"] == hashlib.sha256(
        (out_root / "export" / "messages.jsonl").read_bytes()
    ).hexdigest()


def test_write_manifest_handles_missing_export_files(tmp_path):
    out_root = tmp_path / "output"
    out_root.mkdir(parents=True)
    # No export dir created at all -> files block is empty, no crash.
    path = write_manifest(
        out_root,
        extract_counts={"codex": 1},
        redact_report={"per_source": {"codex": {"identifiers": 2}}},
        export_summaries={},
        config_snapshot={},
    )
    manifest = json.loads(path.read_text())
    assert manifest["files"] == {}
    assert manifest["sources"]["codex"]["raw_records"] == 1
    assert manifest["sources"]["codex"]["redaction_counts"] == {"identifiers": 2}
    # No per-source export attribution (field dropped), and no per-source keys
    # beyond raw_records + redaction_counts.
    assert "exported_messages" not in manifest["sources"]["codex"]
    # Empty export_summaries -> export_summary block present but all zero.
    assert manifest["export_summary"] == {
        "messages_rows": 0,
        "messages_dropped_no_assistant": 0,
        "sharegpt_pairs": 0,
        "sharegpt_dropped_trailing_user": 0,
    }


def test_write_manifest_missing_per_source_key_is_safe(tmp_path):
    # redact_report without a "per_source" key must not raise.
    out_root = tmp_path / "output"
    _seed_export(out_root, messages_rows=1, sharegpt_rows=0)
    path = write_manifest(
        out_root,
        extract_counts={"claude_code": 1},
        redact_report={},
        export_summaries={"messages": {"rows": 1}},
        config_snapshot={"variant": "messages"},
    )
    manifest = json.loads(path.read_text())
    assert manifest["sources"]["claude_code"]["redaction_counts"] == {}
    assert manifest["export_summary"]["messages_rows"] == 1
    # sharegpt.jsonl not present when 0 rows were written (seed wrote empty file).
