"""Independent leftover scanner. Reuses redact.rules patterns for detection only."""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from traces.fastmatch import Prefilter
from traces.parallel import iter_range, line_ranges
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


@lru_cache(maxsize=1)
def _detector() -> tuple[list[tuple[str, re.Pattern]], Prefilter]:
    patterns = _detection_patterns()
    return patterns, Prefilter([pattern for _category, pattern in patterns])


def _matched_category(
    value: str,
    patterns: list[tuple[str, re.Pattern]],
    prefilter: Prefilter | None = None,
) -> str | None:
    # Keep an excluded delimiter in place so ``TOKEN=[PRIVATE_TERM]$NEXT``
    # cannot collapse into a newly valid assignment, while paths containing a
    # placeholder cannot become newly valid home paths during verification.
    value = _PLACEHOLDER_RE.sub(lambda _match: "\\", value)
    candidates = prefilter.candidates(value) if prefilter is not None else None
    for index, (category, pattern) in enumerate(patterns):
        if candidates is not None and index not in candidates:
            continue
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
    line: str | bytes,
    patterns: list[tuple[str, re.Pattern]],
    prefilter: Prefilter | None = None,
) -> tuple[str, str] | None:
    try:
        row = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "invalid_json", "$"
    for json_path, value in _iter_strings(row):
        category = _matched_category(value, patterns, prefilter)
        if category:
            return category, json_path
    return None


def _scan_range(task: tuple[str, int, int]) -> tuple[list[tuple[int, str, str]], int]:
    path, start, end = task
    patterns, prefilter = _detector()
    findings: list[tuple[int, str, str]] = []
    lines = 0
    for lines, line in enumerate(iter_range(Path(path), start, end), start=1):
        finding = _row_finding(line, patterns, prefilter)
        if finding:
            findings.append((lines, *finding))
    return findings, lines


def _clean_range(task: tuple[str, int, int, str]) -> dict[str, Any]:
    path, start, end, part = task
    patterns, prefilter = _detector()
    kept = 0
    dropped: Counter[str] = Counter()
    with open(part, "wb") as target:
        for line in iter_range(Path(path), start, end):
            finding = _row_finding(line, patterns, prefilter)
            if finding:
                dropped[finding[0]] += 1
                continue
            target.write(line)
            kept += 1
    return {"kept": kept, "dropped": dict(dropped)}


def _run(fn, tasks: list, jobs: int) -> list:
    if jobs <= 1 or len(tasks) <= 1:
        return [fn(task) for task in tasks]
    with ProcessPoolExecutor(min(jobs, len(tasks))) as pool:
        return list(pool.map(fn, tasks))


def scan(root: Path, jobs: int = 1) -> ScanReport:
    """Report rows that still match a redaction rule. ``jobs`` > 1 uses processes."""
    files = sorted(root.rglob("*.jsonl"))
    tasks = [(str(path), start, end) for path in files for start, end in line_ranges(path)]
    findings: list[Finding] = []
    offset: dict[str, int] = {}
    for (path, _start, _end), (hits, lines) in zip(tasks, _run(_scan_range, tasks, jobs)):
        base = offset.get(path, 0)
        for line_no, category, json_path in hits:
            findings.append(Finding(Path(path), base + line_no, category, json_path))
        offset[path] = base + lines
    return ScanReport(findings, len(files))


def clean_tree(root: Path, out: Path, jobs: int = 1) -> dict[str, object]:
    """Copy JSONL rows that pass the same rules used by ``scan``."""
    if root.resolve() == out.resolve():
        raise ValueError("clean output must differ from input")
    if out.exists() and any(out.rglob("*.jsonl")):
        raise ValueError("clean output must not contain existing JSONL files")

    out.mkdir(parents=True, exist_ok=True)
    plan: list[tuple[Path, list[str]]] = []
    tasks: list[tuple[str, int, int, str]] = []
    for path in sorted(root.rglob("*.jsonl")):
        destination = out / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        parts = []
        for index, (start, end) in enumerate(line_ranges(path)):
            part = str(destination.with_name(f".{destination.name}.part{index:05d}"))
            tasks.append((str(path), start, end, part))
            parts.append(part)
        plan.append((destination, parts))

    kept = 0
    dropped_by_category: Counter[str] = Counter()
    dropped_by_source: Counter[str] = Counter()
    for (path, *_rest), result in zip(tasks, _run(_clean_range, tasks, jobs)):
        kept += result["kept"]
        dropped_by_category.update(result["dropped"])
        dropped_by_source[Path(path).stem] += sum(result["dropped"].values())

    for destination, parts in plan:
        with destination.open("wb") as target:
            for part in parts:
                with open(part, "rb") as source:
                    while chunk := source.read(1 << 24):
                        target.write(chunk)
                Path(part).unlink()

    return {
        "rows_kept": kept,
        "rows_dropped": sum(dropped_by_category.values()),
        "dropped_by_category": dict(dropped_by_category),
        "dropped_by_source": {k: v for k, v in dropped_by_source.items() if v},
    }
