"""Independent leftover scanner. Reuses redact.rules patterns for detection only."""
from __future__ import annotations

import re
from collections import Counter
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


def _matched_category(
    line: str, patterns: list[tuple[str, re.Pattern]]
) -> str | None:
    for category, pattern in patterns:
        if pattern.search(line):
            return category
    return None


def scan(root: Path) -> ScanReport:
    patterns = _detection_patterns()
    findings: list[Finding] = []
    files = 0
    for path in root.rglob("*.jsonl"):
        files += 1
        with path.open() as f:
            for line_no, line in enumerate(f, start=1):
                category = _matched_category(line, patterns)
                if category:
                    findings.append(Finding(path, line_no, category, line.strip()[:200]))
    return ScanReport(findings, files)


def clean_tree(root: Path, out: Path) -> dict[str, object]:
    """Copy JSONL rows that pass the same rules used by ``scan``."""
    if root.resolve() == out.resolve():
        raise ValueError("clean output must differ from input")
    if out.exists() and any(out.rglob("*.jsonl")):
        raise ValueError("clean output must not contain existing JSONL files")

    patterns = _detection_patterns()
    kept = 0
    dropped = 0
    dropped_by_category: Counter[str] = Counter()
    dropped_by_source: Counter[str] = Counter()

    out.mkdir(parents=True, exist_ok=True)
    for path in sorted(root.rglob("*.jsonl")):
        relative = path.relative_to(root)
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with path.open() as source, destination.open("w") as target:
            for line in source:
                category = _matched_category(line, patterns)
                if category:
                    dropped += 1
                    dropped_by_category[category] += 1
                    dropped_by_source[path.stem] += 1
                    continue
                target.write(line)
                kept += 1

    return {
        "rows_kept": kept,
        "rows_dropped": dropped,
        "dropped_by_category": dict(dropped_by_category),
        "dropped_by_source": dict(dropped_by_source),
    }
