from __future__ import annotations

import json

from tests.conftest import write_jsonl
from traces.config import RedactorConfig
from traces.redact.pipeline import run_redact


def test_pipeline_redacts_canary_jsonl(tmp_path, canary_conversation):
    in_dir = tmp_path / "raw_extracted"
    out_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "claude_code.jsonl", [canary_conversation])

    config = RedactorConfig(user_name="alexmorgan", home_dir="/Users/alexmorgan")
    report = run_redact(in_dir, out_dir, config)

    out_file = out_dir / "claude_code.jsonl"
    assert out_file.exists()
    raw = out_file.read_text()
    # None of the canary values survive
    assert "sk-proj-abcdef" not in raw
    assert "ghp_abcdef" not in raw
    assert "alex.morgan@example.com" not in raw
    assert "/Users/alexmorgan" not in raw
    assert "internal.company.local" not in raw
    # Markers are present
    assert "[SECRET:OPENAI_API_KEY]" in raw
    assert "[SECRET:GITHUB_TOKEN]" in raw
    # Report has counts
    assert report["counts"]["openai_key"] >= 1
    assert report["counts"]["github_token"] >= 1


def test_pipeline_writes_redaction_report(tmp_path, canary_conversation):
    in_dir = tmp_path / "raw_extracted"
    out_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "claude_code.jsonl", [canary_conversation])

    run_redact(in_dir, out_dir, RedactorConfig(user_name="alexmorgan", home_dir="/Users/alexmorgan"))

    report_file = out_dir.parent / "REDACTION_REPORT.json"
    assert report_file.exists()
    report = json.loads(report_file.read_text())
    assert "counts" in report
    assert "config" in report


def test_pipeline_empty_input_writes_empty_output(tmp_path):
    in_dir = tmp_path / "raw_extracted"
    out_dir = tmp_path / "redacted"
    in_dir.mkdir(parents=True)
    (in_dir / "empty.jsonl").write_text("")

    config = RedactorConfig(user_name="x", home_dir="/home/x")
    report = run_redact(in_dir, out_dir, config)
    assert report["counts"] == {} or all(v == 0 for v in report["counts"].values())
