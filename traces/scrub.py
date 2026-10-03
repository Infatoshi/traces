"""Exact-value secret scrub for JSONL trees.

Replaces every occurrence of known secret values (from ``harvest.py`` output,
``LABEL<TAB>VALUE`` per line) with ``[SECRET:KNOWN_VALUE]``. Values are matched
in raw and JSON-escaped form with Aho-Corasick; if a raw replacement would break
a line's JSON, the line is decoded and the values are replaced inside strings.
Reports only labels and counts, never values.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import ahocorasick

from traces.parallel import iter_range, line_ranges

PLACEHOLDER = "[SECRET:KNOWN_VALUE]"
MIN_LEN = 8

_AUTOMATON: ahocorasick.Automaton | None = None


def load_values(files: list[Path]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in files:
        for line in path.read_text().splitlines():
            if "\t" in line:
                label, value = line.split("\t", 1)
                out.append((label, value))
    return out


def build(values: list[tuple[str, str]]) -> ahocorasick.Automaton:
    automaton = ahocorasick.Automaton()
    for label, value in values:
        forms = {value, json.dumps(value)[1:-1]}
        try:  # values reported from raw JSON text (e.g. gitleaks) also match decoded strings
            forms.add(json.loads(f'"{value}"'))
        except ValueError:
            pass
        for form in forms:
            if len(form) >= MIN_LEN:
                automaton.add_word(form, (label, form))
    automaton.make_automaton()
    return automaton


def _spans(automaton, text: str) -> list[tuple[int, int, str]]:
    found = sorted((end - len(form) + 1, end + 1, label) for end, (label, form) in automaton.iter(text))
    merged: list[tuple[int, int, str]] = []
    for start, end, label in found:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), merged[-1][2])
        else:
            merged.append((start, end, label))
    return merged


def _replace(text: str, spans: list[tuple[int, int, str]]) -> str:
    parts, prev = [], 0
    for start, end, _label in spans:
        parts.append(text[prev:start])
        parts.append(PLACEHOLDER)
        prev = end
    parts.append(text[prev:])
    return "".join(parts)


def _decoded_replace(automaton, line: str) -> str:
    def walk(obj: Any) -> Any:
        if isinstance(obj, str):
            spans = _spans(automaton, obj)
            return _replace(obj, spans) if spans else obj
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        return obj

    return json.dumps(walk(json.loads(line)), ensure_ascii=False) + "\n"


def _init(values: list[tuple[str, str]]) -> None:
    global _AUTOMATON
    _AUTOMATON = build(values)


def _scrub_lines(path: str, start: int, end: int, out, hits: Counter[str]) -> None:
    assert _AUTOMATON is not None
    for raw in iter_range(Path(path), start, end):
        line = raw.decode("utf-8", errors="surrogateescape")
        spans = _spans(_AUTOMATON, line)
        for _s, _e, label in spans:
            hits[label] += 1
        if out is None:
            continue
        if spans:
            new = _replace(line, spans)
            try:
                json.loads(new)
            except ValueError:
                new = _decoded_replace(_AUTOMATON, line)
            line = new
        out.write(line)


def _scrub_range(task: tuple[str, int, int, str | None]) -> dict[str, int]:
    path, start, end, part = task
    hits: Counter[str] = Counter()
    if part is None:
        _scrub_lines(path, start, end, None, hits)
    else:
        with open(part, "w", encoding="utf-8", errors="surrogateescape") as out:
            _scrub_lines(path, start, end, out, hits)
    return dict(hits)


def scrub_tree(root: Path, values: list[tuple[str, str]], fix: bool, jobs: int) -> dict[str, Any]:
    """Count (and with ``fix``, replace in place) known values in every *.jsonl."""
    tasks, plan = [], []
    for path in sorted(root.rglob("*.jsonl")):
        parts = []
        for index, (start, end) in enumerate(line_ranges(path)):
            part = str(path.with_name(f".{path.name}.scrub{index:05d}")) if fix else None
            tasks.append((str(path), start, end, part))
            parts.append(part)
        plan.append((path, parts))
    by_label: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    with ProcessPoolExecutor(max(1, min(jobs, len(tasks) or 1)), initializer=_init,
                             initargs=(values,)) as pool:
        for (path, *_rest), hits in zip(tasks, pool.map(_scrub_range, tasks)):
            by_label.update(hits)
            by_file[Path(path).name] += sum(hits.values())
    if fix:
        for path, parts in plan:
            tmp = path.with_name(f".{path.name}.scrubbed")
            with tmp.open("wb") as target:
                for part in parts:
                    with open(part, "rb") as source:
                        while chunk := source.read(1 << 24):
                            target.write(chunk)
                    os.unlink(part)
            os.replace(tmp, path)
    return {
        "values": len(values),
        "occurrences": sum(by_label.values()),
        "by_file": {k: v for k, v in by_file.items() if v},
        "by_label": dict(by_label.most_common()),
    }
