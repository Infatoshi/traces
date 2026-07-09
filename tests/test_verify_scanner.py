from __future__ import annotations

import json

from traces.verify.scanner import scan


def test_clean_redacted_dir_passes(tmp_path):
    (tmp_path / "claude_code.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "[SECRET:OPENAI_API_KEY] cleaned"}]}) + "\n"
    )
    report = scan(tmp_path)
    assert report.ok, report.leftovers


def test_dir_with_openai_key_fails(tmp_path):
    (tmp_path / "x.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD"}]}) + "\n"
    )
    report = scan(tmp_path)
    assert not report.ok
    assert any(f.category == "openai_key" for f in report.leftovers)


def test_dir_with_email_fails(tmp_path):
    (tmp_path / "x.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "contact alex.morgan@example.com"}]}) + "\n"
    )
    report = scan(tmp_path)
    assert not report.ok
    assert any(f.category == "email" for f in report.leftovers)


def test_scanner_checks_filenames(tmp_path):
    """Path names can also leak (e.g. username in encoded form)."""
    (tmp_path / "alexmorgan-traces.jsonl").write_text("{}\n")
    # username "alexmorgan" is not a default scanner rule; we use a private_term
    # via the report interface. For the default scan, filename alone passes.
    report = scan(tmp_path)
    assert report.ok  # no rule hit
