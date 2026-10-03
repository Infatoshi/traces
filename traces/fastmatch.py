"""Multi-pattern prefilter for the redaction and verification regex sets.

Hyperscan (Vectorscan on ARM) scans a string once for every pattern at the
same time. Each pattern is compiled with ``HS_FLAG_PREFILTER``, which turns
constructs Hyperscan cannot run exactly (lookarounds, backreferences) into a
superset, so a pattern Hyperscan does not report cannot match. Callers still
run the original Python regex on the reported candidates, so results are
identical to running every Python regex; only the patterns that cannot match
are skipped.

Without the ``hyperscan`` package, or for text that is not valid UTF-8,
``candidates`` returns ``None`` and callers run every pattern.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

try:
    import hyperscan
except ImportError:  # pragma: no cover - exercised on platforms without wheels
    hyperscan = None


class Prefilter:
    def __init__(self, patterns: Sequence[re.Pattern[str]]) -> None:
        self.size = len(patterns)
        self.always: set[int] = set()
        self._db = _compile(tuple((p.pattern, p.flags) for p in patterns), self.always)

    @property
    def active(self) -> bool:
        return self._db is not None

    def candidates(self, text: str) -> set[int] | None:
        """Indexes of patterns that may match ``text``; ``None`` means all."""
        if self._db is None:
            return None
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError:
            return None
        hits = set(self.always)

        def on_match(pattern_id, _start, _end, _flags, _context):
            hits.add(pattern_id)

        self._db.scan(data, match_event_handler=on_match)
        return hits


_CACHE: dict[tuple, tuple[object, frozenset[int]]] = {}


def _flags(py_flags: int) -> int:
    flag = (
        hyperscan.HS_FLAG_PREFILTER
        | hyperscan.HS_FLAG_SINGLEMATCH
        | hyperscan.HS_FLAG_UTF8
        | hyperscan.HS_FLAG_UCP
        | hyperscan.HS_FLAG_ALLOWEMPTY
    )
    if py_flags & re.IGNORECASE:
        flag |= hyperscan.HS_FLAG_CASELESS
    if py_flags & re.DOTALL:
        flag |= hyperscan.HS_FLAG_DOTALL
    if py_flags & re.MULTILINE:
        flag |= hyperscan.HS_FLAG_MULTILINE
    return flag


def _build(entries: list[tuple[int, bytes, int]]):
    db = hyperscan.Database()
    db.compile(
        expressions=[e for _i, e, _f in entries],
        ids=[i for i, _e, _f in entries],
        elements=len(entries),
        flags=[f for _i, _e, f in entries],
    )
    return db


def _compile(key: tuple[tuple[str, int], ...], always: set[int]):
    """Compile once per pattern set; patterns Hyperscan rejects always run."""
    if hyperscan is None or not key:
        return None
    if key not in _CACHE:
        entries = [(i, pat.encode("utf-8"), _flags(fl)) for i, (pat, fl) in enumerate(key)]
        rejected: set[int] = set()
        try:
            db = _build(entries)
        except Exception:
            ok = []
            for entry in entries:
                try:
                    _build([entry])
                    ok.append(entry)
                except Exception:
                    rejected.add(entry[0])
            db = _build(ok) if ok else None
        _CACHE[key] = (db, frozenset(rejected))
    db, rejected = _CACHE[key]
    always.update(rejected)
    return db
