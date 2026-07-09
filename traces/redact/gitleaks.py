"""Optional gitleaks layer: secret scanning + scrubbing.

Ported from upstream lines 873-991 of export_redacted_traces.py
(``run_gitleaks_scan`` and ``scrub_gitleaks_findings``).

Public API (used by ``pipeline.py`` and tests):
- ``run_scan(root)``      — final no-redact scan; returns a report dict.
- ``scrub_findings(root)``— iterative in-place redaction of detected secrets.

Both resolve the gitleaks binary via ``shutil.which`` and shell out via
``subprocess.run``. They return ``{"available": False, "status": "skipped", ...}``
when gitleaks is not installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _resolve_binary() -> str | None:
    """Locate the gitleaks binary. ``shutil.which`` only; no legacy paths."""
    return shutil.which("gitleaks")


def run_scan(root: Path) -> dict[str, Any]:
    """Run a final gitleaks scan with ``--redact=100`` over ``root``.

    The report is written *inside* ``root`` so the tree we walk is the
    tree gitleaks reads (and the report is easy to find from the caller).
    """
    binary = _resolve_binary()
    report_path = root / "gitleaks-final-report.json"
    if binary is None:
        return {
            "available": False,
            "status": "skipped",
            "reason": "gitleaks binary not found",
        }
    if report_path.exists():
        report_path.unlink()
    cmd = [
        binary,
        "dir",
        str(root),
        "--redact=100",
        "--report-format", "json",
        "--report-path", str(report_path),
    ]
    proc = subprocess.run(
        cmd, cwd=str(root), text=True, capture_output=True
    )
    findings: int | None = None
    if report_path.exists():
        try:
            findings = len(json.loads(report_path.read_text(encoding="utf-8") or "[]"))
        except Exception:
            findings = None
    return {
        "available": True,
        "status": "clean" if proc.returncode == 0 else "findings_or_error",
        "exit_code": proc.returncode,
        "findings": findings,
        "report": str(report_path),
        "stderr_tail": proc.stderr[-2000:] if proc.stderr else "",
    }


def scrub_findings(root: Path, max_rounds: int = 3) -> dict[str, Any]:
    """Iteratively scan ``root`` and redact detected secrets in place.

    For each finding: try an exact-text replacement first; if that fails
    on a ``.jsonl`` file, tombstone the offending record entirely.
    """
    binary = _resolve_binary()
    if binary is None:
        return {
            "available": False,
            "status": "skipped",
            "reason": "gitleaks binary not found",
        }
    summary: dict[str, Any] = {"available": True, "rounds": []}
    for round_index in range(1, max_rounds + 1):
        report_path = root / f".gitleaks-fix-round-{round_index}.json"
        if report_path.exists():
            report_path.unlink()
        cmd = [
            binary,
            "dir",
            str(root),
            "--redact=0",
            "--report-format", "json",
            "--report-path", str(report_path),
        ]
        subprocess.run(cmd, cwd=str(root), text=True, capture_output=True)
        findings = (
            json.loads(report_path.read_text(encoding="utf-8") or "[]")
            if report_path.exists() else []
        )
        report_path.unlink(missing_ok=True)
        round_summary: dict[str, Any] = {
            "round": round_index,
            "findings": len(findings),
            "files_changed": 0,
            "exact_replacements": 0,
            "records_tombstoned": 0,
            "by_rule": {},
        }
        if not findings:
            summary["rounds"].append(round_summary)
            summary["status"] = "clean"
            return summary
        for item in findings:
            secret = item.get("Secret") or ""
            file_name = item.get("File") or ""
            rule = item.get("RuleID") or "secret"
            path = Path(file_name)
            if not path.exists() or not str(path).startswith(str(root) + "/"):
                continue
            changed = False
            if secret:
                text = path.read_text(encoding="utf-8", errors="ignore")
                placeholder = "[SECRET:GITLEAKS_" + "".join(
                    ch if ch.isalnum() else "_" for ch in rule.upper()
                ) + "]"
                new = text.replace(secret, placeholder)
                if new != text:
                    path.write_text(new, encoding="utf-8")
                    count = text.count(secret)
                    round_summary["files_changed"] += 1
                    round_summary["exact_replacements"] += count
                    round_summary["by_rule"][rule] = (
                        round_summary["by_rule"].get(rule, 0) + count
                    )
                    changed = True
            if not changed and str(path).endswith(".jsonl"):
                start_line = int(item.get("StartLine") or 0)
                lines = path.read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines()
                if 1 <= start_line <= len(lines):
                    try:
                        old = json.loads(lines[start_line - 1])
                        record_type = (
                            old.get("type", "redacted_record")
                            if isinstance(old, dict) else "redacted_record"
                        )
                    except Exception:
                        record_type = "redacted_record"
                    lines[start_line - 1] = json.dumps(
                        {
                            "type": record_type,
                            "redacted": True,
                            "redaction_reason": "gitleaks_" + rule,
                        },
                        separators=(",", ":"),
                    )
                    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    round_summary["files_changed"] += 1
                    round_summary["records_tombstoned"] += 1
                    round_summary["by_rule"][rule] = (
                        round_summary["by_rule"].get(rule, 0) + 1
                    )
        summary["rounds"].append(round_summary)
    summary["status"] = "max_rounds_reached"
    return summary
