"""Independent leftover scanner. Reuses redact.rules patterns for detection only."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from traces.redact.rules import secrets, identifiers, paths, urls
from traces.redact.rules import RuleContext


@dataclass
class Finding:
    path: Path
    line_no: int
    category: str
    snippet: str


@dataclass
class ScanReport:
    leftovers: list[Finding]
    files_scanned: int

    @property
    def ok(self) -> bool:
        return not self.leftovers


def _detection_patterns() -> list[tuple[str, re.Pattern]]:
    """Detection-only patterns (no replacement). Reuses rules module patterns."""
    ctx = RuleContext(user_name="__never_match__", home_dir="__never_match__")
    patterns: list[tuple[str, re.Pattern]] = []
    for family in (secrets, identifiers, paths, urls):
        for category, pat, _rep in family.build(ctx):
            patterns.append((category, pat))
    return patterns


def scan(root: Path) -> ScanReport:
    patterns = _detection_patterns()
    findings: list[Finding] = []
    files = 0
    for path in root.rglob("*.jsonl"):
        files += 1
        with path.open() as f:
            for line_no, line in enumerate(f, start=1):
                for category, pat in patterns:
                    if pat.search(line):
                        findings.append(Finding(path, line_no, category, line.strip()[:200]))
                        break  # one finding per line is enough
    return ScanReport(findings, files)
