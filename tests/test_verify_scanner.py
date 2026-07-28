from __future__ import annotations

import json

import pytest

from traces.verify.scanner import clean_tree, scan


def test_clean_redacted_dir_passes(tmp_path):
    (tmp_path / "claude_code.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "[SECRET:OPENAI_API_KEY] cleaned"}]}) + "\n"
    )
    report = scan(tmp_path)
    assert report.ok, report.leftovers


def test_redactor_placeholders_do_not_retrigger_rules(tmp_path):
    (tmp_path / "x.jsonl").write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "API_TOKEN=[PRIVATE_TERM:0001:abc123] "
                        "subsequent_word_is_not_a_secret "
                        "[REDACTED:SENSITIVE_FIELD] "
                        "/home/[PRIVATE_TERM]/project "
                        "[EMAIL:0001:abc123]",
                    }
                ]
            }
        )
        + "\n"
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


def test_json_escape_before_decorator_is_not_an_email(tmp_path):
    (tmp_path / "x.jsonl").write_text(
        json.dumps({"content": r"code\n@pytest.mark.parametrize"}) + "\n"
    )
    report = scan(tmp_path)
    assert report.ok, report.leftovers


def test_scanner_checks_filenames(tmp_path):
    """Path names can also leak (e.g. username in encoded form)."""
    (tmp_path / "alexmorgan-traces.jsonl").write_text("{}\n")
    # username "alexmorgan" is not a default scanner rule; we use a private_term
    # via the report interface. For the default scan, filename alone passes.
    report = scan(tmp_path)
    assert report.ok  # no rule hit


def test_clean_tree_drops_complete_flagged_rows(tmp_path):
    source = tmp_path / "redacted"
    output = tmp_path / "clean"
    source.mkdir()
    rows = [
        {"messages": [{"role": "user", "content": "safe"}]},
        {"messages": [{"role": "user", "content": "alex@example.com"}]},
    ]
    (source / "codex.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )

    report = clean_tree(source, output)

    assert report["rows_kept"] == 1
    assert report["rows_dropped"] == 1
    assert report["dropped_by_source"] == {"codex": 1}
    assert scan(output).ok
    assert "safe" in (output / "codex.jsonl").read_text()
    assert "example.com" not in (output / "codex.jsonl").read_text()


def test_clean_tree_refuses_in_place_operation(tmp_path):
    with pytest.raises(ValueError, match="must differ"):
        clean_tree(tmp_path, tmp_path)


def test_invalid_json_is_flagged_and_dropped(tmp_path):
    source = tmp_path / "redacted"
    output = tmp_path / "clean"
    source.mkdir()
    (source / "x.jsonl").write_text('{"valid": true}\n{"broken":\n')

    scan_report = scan(source)
    assert not scan_report.ok
    assert scan_report.leftovers[0].category == "invalid_json"
    assert scan_report.leftovers[0].snippet == "$"

    clean_report = clean_tree(source, output)
    assert clean_report["rows_kept"] == 1
    assert clean_report["rows_dropped"] == 1
    assert clean_report["dropped_by_category"] == {"invalid_json": 1}
    assert json.loads((output / "x.jsonl").read_text()) == {"valid": True}
