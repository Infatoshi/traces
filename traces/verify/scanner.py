"""Independent leftover scanner. Reuses redact.rules patterns for detection only."""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from traces.redact.rules import UPSTREAM_ORDER, RuleContext, identifiers, paths, secrets, urls

_FIXED_PLACEHOLDER_HEADS = {
    "ENCODED_HOME_PATH",
    "HOME_PATH",
    "HW",
    "LLM_PII",
    "OMITTED",
    "PII",
    "PII_MODEL",
    "PRIVATE_DOMAIN",
    "PRIVATE_NAME",
    "PRIVATE_SHARE_URL",
    "PRIVATE_TERM",
    "PRIVATE_URL",
    "PRIVATE_USER",
    "REDACTED",
    "SECRET",
}
_PLACEHOLDER_HEADS = tuple(sorted(
    _FIXED_PLACEHOLDER_HEADS | {category.upper() for category in UPSTREAM_ORDER},
    key=lambda value: (-len(value), value),
))
_PLACEHOLDER_RE = re.compile(
    rf"\[(?:{'|'.join(_PLACEHOLDER_HEADS)})(?::[A-Za-z0-9_.:-]+)*\]"
)


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


def _matched_category(value: str, patterns: list[tuple[str, re.Pattern]]) -> str | None:
    # Keep an excluded delimiter in place so ``TOKEN=[PRIVATE_TERM]$NEXT``
    # cannot collapse into a newly valid assignment, while paths containing a
    # placeholder cannot become newly valid home paths during verification.
    value = _PLACEHOLDER_RE.sub(lambda _match: "\\", value)
    for category, pattern in patterns:
        for match in pattern.finditer(value):
            if (
                category == "email"
                and match.start() > 0
                and value[match.start() - 1] == "\\"
                and match.group(0)[0].lower() in "bfnrtu"
            ):
                # JSON rendering turns a real newline before decorators such
                # as ``@pytest.mark`` into ``\\n@pytest.mark``. The email rule
                # sees ``n@pytest.mark`` unless the escape boundary is kept.
                continue
            return category
    return None


def _iter_strings(value: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    """Yield strings and keys without putting possibly-private keys in paths."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            if isinstance(key, str):
                yield f"{path}[key:{index}]", key
            yield from _iter_strings(item, f"{path}[value:{index}]")


def _row_finding(
    line: str, patterns: list[tuple[str, re.Pattern]]
) -> tuple[str, str] | None:
    try:
        row = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "invalid_json", "$"
    for json_path, value in _iter_strings(row):
        category = _matched_category(value, patterns)
        if category:
            return category, json_path
    return None


def scan(root: Path) -> ScanReport:
    patterns = _detection_patterns()
    findings: list[Finding] = []
    files = 0
    for path in root.rglob("*.jsonl"):
        files += 1
        with path.open() as f:
            for line_no, line in enumerate(f, start=1):
                finding = _row_finding(line, patterns)
                if finding:
                    category, json_path = finding
                    findings.append(Finding(path, line_no, category, json_path))
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
                finding = _row_finding(line, patterns)
                if finding:
                    category, _json_path = finding
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
