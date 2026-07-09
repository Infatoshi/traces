"""Tests for the optional redaction layers (Task 13).

Heavy deps are mocked:
- gitleaks: ``subprocess`` is patched so we don't need the binary.
- privacy-filter: we never instantiate it (torch/transformers absent);
  the load-bearing test asserts the core package imports without those deps.
- llm-residue: ``ask_model`` is patched so no real LLM server is hit.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from traces.redact import gitleaks, llm_residue


# ---------------------------------------------------------------------------
# gitleaks layer
# ---------------------------------------------------------------------------

def test_gitleaks_run_scan_no_findings(tmp_path):
    """Mock subprocess so we don't need the gitleaks binary installed."""
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    (redacted / "x.jsonl").write_text('{"messages":[]}\n')

    report_path = redacted / "gitleaks-final-report.json"

    def _fake_run(cmd, *a, **k):
        report_path.write_text("[]")  # clean
        return MagicMock(returncode=0, stderr="")

    with patch.object(gitleaks, "subprocess") as mock_subp, \
         patch.object(gitleaks, "_resolve_binary", return_value="/fake/gitleaks"):
        mock_subp.run.side_effect = _fake_run
        report = gitleaks.run_scan(redacted)
    assert report["available"] is True
    assert report["findings"] == 0


def test_gitleaks_run_scan_reports_findings(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    (redacted / "x.jsonl").write_text('{"messages":[]}\n')

    report_path = redacted / "gitleaks-final-report.json"

    def _fake_run(cmd, *a, **k):
        report_path.write_text(json.dumps([{"RuleID": "aws"}]))
        return MagicMock(returncode=1, stderr="leak")

    with patch.object(gitleaks, "subprocess") as mock_subp, \
         patch.object(gitleaks, "_resolve_binary", return_value="/fake/gitleaks"):
        mock_subp.run.side_effect = _fake_run
        report = gitleaks.run_scan(redacted)
    assert report["findings"] == 1
    assert report["status"] == "findings_or_error"


def test_gitleaks_run_scan_skipped_when_no_binary(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    with patch.object(gitleaks, "_resolve_binary", return_value=None):
        report = gitleaks.run_scan(redacted)
    assert report["available"] is False
    assert report["status"] == "skipped"


def test_gitleaks_scrub_redacts_secret_in_place(tmp_path):
    """scrub_findings should replace a detected secret with a placeholder."""
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    target = redacted / "trace.jsonl"
    secret = "AKIAABCDEFGHIJKLMNOP"
    target.write_text(json.dumps({"content": "key=" + secret}) + "\n")

    # Fake finding pointing at our secret inside `target`.
    fake_findings = [{
        "Secret": secret,
        "File": str(target),
        "RuleID": "aws-access-token",
        "StartLine": 1,
    }]
    report_path_holder = {"round": 1}

    def _run(cmd, *a, **k):
        # gitleaks writes the round report; first round has findings, second clean.
        rp = Path(cmd[cmd.index("--report-path") + 1])
        if report_path_holder["round"] == 1:
            rp.write_text(json.dumps(fake_findings))
        else:
            rp.write_text("[]")
        report_path_holder["round"] += 1
        return MagicMock(returncode=1, stderr="")

    with patch.object(gitleaks, "subprocess") as mock_subp, \
         patch.object(gitleaks, "_resolve_binary", return_value="/fake/gitleaks"):
        mock_subp.run.side_effect = _run
        report = gitleaks.scrub_findings(redacted, max_rounds=3)

    assert report["status"] == "clean"
    new_text = target.read_text()
    assert secret not in new_text
    assert "[SECRET:GITLEAKS_AWS_ACCESS_TOKEN]" in new_text


# ---------------------------------------------------------------------------
# privacy-filter import isolation (the load-bearing requirement)
# ---------------------------------------------------------------------------

def test_privacy_filter_not_imported_at_module_load():
    """Core import must succeed even without torch/transformers installed.

    We force a reload of the pipeline module and assert it does not pull in
    ``transformers`` or ``torch`` at import time.
    """
    import traces.redact.pipeline as pipeline_mod
    importlib.reload(pipeline_mod)
    # The privacy_filter module itself must import without torch/transformers.
    import traces.redact.privacy_filter as pf_mod
    importlib.reload(pf_mod)
    # If a top-level torch/transformers import leaked, these would be present
    # *because of our modules*. We assert the privacy_filter module's globals
    # do not contain torch/transformers (lazy import lives inside __init__).
    assert "torch" not in pf_mod.__dict__
    assert "transformers" not in pf_mod.__dict__
    assert getattr(pipeline_mod, "run_redact") is not None


# ---------------------------------------------------------------------------
# llm-residue layer
# ---------------------------------------------------------------------------

def test_llm_residue_pass_applies_model_redactions(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    target = redacted / "trace.jsonl"
    # The candidate regex matches "location", so this line will be sent.
    target.write_text(
        json.dumps({"content": "meet at location Acme HQ"}) + "\n"
    )

    def _fake_ask_model(base_url, model, chunk, timeout):
        # Model agrees "Acme HQ" should be redacted and it really is in chunk.
        if "Acme HQ" in chunk:
            return [{"text": "Acme HQ", "category": "LOCATION"}]
        return []

    with patch.object(llm_residue, "discover_model", return_value="fake-model"), \
         patch.object(llm_residue, "ask_model", side_effect=_fake_ask_model):
        report = llm_residue.llm_residue_pass(redacted, base_url="http://x")

    assert report["files_changed"] == 1
    assert report["replacements"] == 1
    new_text = target.read_text()
    assert "Acme HQ" not in new_text
    assert "[LLM_PII:LOCATION]" in new_text


def test_llm_residue_pass_no_candidates(tmp_path):
    """Files with no candidate-pattern hits produce no work."""
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    (redacted / "boring.jsonl").write_text('{"content":"hello world"}\n')

    with patch.object(llm_residue, "discover_model", return_value="fake-model"), \
         patch.object(llm_residue, "ask_model") as mock_ask:
        report = llm_residue.llm_residue_pass(redacted, base_url="http://x")
    assert report["chunks_reviewed"] == 0
    assert report["files_changed"] == 0
    mock_ask.assert_not_called()


# ---------------------------------------------------------------------------
# pipeline wiring
# ---------------------------------------------------------------------------

def test_pipeline_runs_gitleaks_when_flagged(tmp_path):
    """End-to-end: pipeline calls run_scan when config.gitleaks is True."""
    from traces.config import RedactorConfig
    from traces.redact import pipeline
    from tests.conftest import write_jsonl, make_conversation, make_message

    in_dir = tmp_path / "raw_extracted"
    out_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "claude_code.jsonl", [
        make_conversation(messages=[
            make_message("user", "hi"),
            make_message("assistant", "yo"),
        ]),
    ])

    config = RedactorConfig(
        user_name="tester", home_dir="/home/tester",
        gitleaks=True,
    )
    with patch(
        "traces.redact.gitleaks.run_scan",
        return_value={"available": True, "findings": 0, "status": "clean"},
    ) as mock_scan:
        report = pipeline.run_redact(in_dir, out_dir, config)
    mock_scan.assert_called_once()
    assert report["layers"]["gitleaks"]["findings"] == 0


def test_pipeline_skips_layers_when_not_flagged(tmp_path):
    """No layers key should appear when no flags are set."""
    from traces.config import RedactorConfig
    from traces.redact import pipeline
    from tests.conftest import write_jsonl, make_conversation, make_message

    in_dir = tmp_path / "raw_extracted"
    out_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "claude_code.jsonl", [
        make_conversation(messages=[
            make_message("user", "hi"),
            make_message("assistant", "yo"),
        ]),
    ])
    config = RedactorConfig(user_name="tester", home_dir="/home/tester")
    report = pipeline.run_redact(in_dir, out_dir, config)
    assert report["layers"] == {}


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------

def test_cli_redact_passes_gitleaks_flag(tmp_path):
    """--gitleaks should flow through to the config the pipeline sees."""
    from traces.cli import cli
    from tests.conftest import write_jsonl, make_conversation, make_message

    raw_dir = tmp_path / "raw"
    write_jsonl(raw_dir / "claude_code.jsonl", [
        make_conversation(messages=[
            make_message("user", "hi"),
            make_message("assistant", "yo"),
        ]),
    ])

    captured = {}

    def _fake_run_redact(in_dir, out_dir, config):
        captured["config"] = config
        return {"counts": {}, "layers": {}, "per_source": {}, "config": {}}

    runner = CliRunner()
    with patch(
        "traces.redact.pipeline.run_redact",
        side_effect=_fake_run_redact,
    ):
        result = runner.invoke(cli, [
            "redact", "--in", str(raw_dir), "--out", str(tmp_path / "red"),
            "--user", "tester", "--home", str(tmp_path),
            "--gitleaks", "--gitleaks-fix",
            "--privacy-filter-device", "cpu",
            "--llm-residue", "http://localhost:8000",
        ])
    assert result.exit_code == 0, result.output
    cfg = captured["config"]
    assert cfg.gitleaks is True
    assert cfg.gitleaks_fix is True
    assert cfg.llm_residue_url == "http://localhost:8000"
    # --privacy-filter-device flows through even without --privacy-filter set.
    assert cfg.privacy_filter_device == "cpu"
