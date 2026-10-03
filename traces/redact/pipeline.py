"""Orchestrates the layered redaction pipeline."""
from __future__ import annotations

import atexit
import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from traces.config import RedactorConfig
from traces.export.metadata import _key_bytes
from traces.parallel import iter_range, line_ranges
from traces.redact.cache import RedactionCache, rules_fingerprint
from traces.redact.discovery import discover_private_terms
from traces.redact.rules import RuleContext, make_redactor
from traces.redact.rules.placeholders import PlaceholderBook
from traces.redact.schema import redact_obj

_CONTENT_KEYS = ("messages", "conversations")


class RedactionPipeline:
    def __init__(self, config: RedactorConfig, cache_shard: str | None = None) -> None:
        self.config = config
        home_dir = Path(config.home_dir) if config.home_dir else Path.home()
        user_name = config.user_name or home_dir.name
        private_terms = list(config.private_terms)
        for term in discover_private_terms(home_dir, user_name, home_dir):
            if term not in private_terms:
                private_terms.append(term)
        ctx = RuleContext(
            user_name=user_name,
            home_dir=str(home_dir),
            private_terms=private_terms,
            private_domains=config.private_domains,
            allow_public_urls=config.allow_public_urls,
            allowed_domains=set(config.allowed_domains),
        )
        key = _key_bytes(config.placeholder_key)
        self.book = PlaceholderBook(key)
        self._redact_str = make_redactor(ctx, self.book)
        self.cache: RedactionCache | None = None
        self.fingerprint = rules_fingerprint(ctx, key)
        # Export rows repeat the same message prefixes across execution rows and
        # were already redacted once; caching them only bloats the store.
        if config.cache_dir and not config.export_rows:
            self.cache = RedactionCache(
                Path(config.cache_dir).expanduser(), self.fingerprint, shard=cache_shard
            )

    def redact_record(self, record: dict[str, Any]) -> dict[str, Any]:
        if self.config.export_rows:
            # Export rows: only the content lists; metadata is tool-generated.
            return {
                key: self._redact_messages(item)
                if key in _CONTENT_KEYS and isinstance(item, list) else item
                for key, item in record.items()
            }
        messages = record.get("messages") if isinstance(record, dict) else None
        if self.cache is None or not isinstance(messages, list):
            return redact_obj(record, self._redact_str, self.book)
        out: dict[Any, Any] = {}
        for key, item in record.items():
            if key == "messages":
                out[self._redact_str(key)] = self._redact_messages(item)
            else:
                out.update(redact_obj({key: item}, self._redact_str, self.book))
        return out

    def _redact_messages(self, messages: list[Any]) -> list[Any]:
        """Redact message blocks, reusing cached output for unchanged ones."""
        if self.cache is None:
            return [redact_obj(m, self._redact_str, self.book) for m in messages]
        keys = [
            self.cache.key(json.dumps(m, separators=(",", ":")).encode())
            for m in messages
        ]
        found = self.cache.get_many(keys)
        out = []
        for message, key in zip(messages, keys):
            hit = found.get(key)
            if hit is not None:
                value, counts = hit
                self.book.counts.update(counts)
                self.cache.hits += 1
            else:
                before = self.book.counts.copy()
                value = redact_obj(message, self._redact_str, self.book)
                delta = self.book.counts - before
                self.cache.put(key, value, delta)
                found[key] = (value, dict(delta))
                self.cache.misses += 1
            out.append(value)
        return out

    def redact_file(self, src: Path, dst: Path) -> dict[str, int]:
        dst.parent.mkdir(parents=True, exist_ok=True)
        before = self.book.counts.copy()
        with src.open() as fin, dst.open("w") as fout:
            for line in fin:
                if not line.strip():
                    continue
                record = json.loads(line)
                cleaned = self.redact_record(record)
                fout.write(json.dumps(cleaned, ensure_ascii=False) + "\n")
        if self.cache is not None:
            self.cache.flush()
        delta: dict[str, int] = {}
        for k, v in self.book.counts.items():
            d = v - before.get(k, 0)
            if d:
                delta[k] = d
        return delta


_WORKER: RedactionPipeline | None = None


def _init_worker(config: RedactorConfig) -> None:
    global _WORKER
    _WORKER = RedactionPipeline(config, cache_shard=str(os.getpid()))
    if _WORKER.cache is not None:
        atexit.register(_WORKER.cache.close)


def _redact_range(task: tuple[str, int, int, str]) -> dict[str, Any]:
    src, start, end, part = task
    assert _WORKER is not None
    before = _WORKER.book.counts.copy()
    cache = _WORKER.cache
    hits, misses = (cache.hits, cache.misses) if cache else (0, 0)
    with open(part, "w", encoding="utf-8") as fout:
        for line in iter_range(Path(src), start, end):
            if not line.strip():
                continue
            cleaned = _WORKER.redact_record(json.loads(line))
            fout.write(json.dumps(cleaned, ensure_ascii=False) + "\n")
    if cache is not None:
        cache.flush()
    return {
        "counts": {
            k: v - before.get(k, 0)
            for k, v in _WORKER.book.counts.items()
            if v - before.get(k, 0)
        },
        "cache_hits": cache.hits - hits if cache else 0,
        "cache_misses": cache.misses - misses if cache else 0,
    }


def _redact_parallel(
    in_dir: Path, out_dir: Path, config: RedactorConfig, jobs: int
) -> tuple[dict[str, dict[str, int]], Counter]:
    """Redact newline-aligned byte ranges in worker processes.

    Rows are never split, so placeholders stay consistent within a trace;
    placeholder sequence numbers are per worker rather than global.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, int, int, str]] = []
    plan: list[tuple[Path, list[str]]] = []
    for src in sorted(in_dir.glob("*.jsonl")):
        dst = out_dir / src.name
        parts = []
        for index, (start, end) in enumerate(line_ranges(src)):
            part = str(dst.with_name(f".{dst.name}.part{index:05d}"))
            tasks.append((str(src), start, end, part))
            parts.append(part)
        plan.append((dst, parts))
    per_source: dict[str, Counter] = {dst.stem: Counter() for dst, _ in plan}
    cache_stats: Counter = Counter()
    with ProcessPoolExecutor(jobs, initializer=_init_worker, initargs=(config,)) as pool:
        for (src, *_rest), result in zip(tasks, pool.map(_redact_range, tasks)):
            per_source[Path(src).stem].update(result["counts"])
            cache_stats["hits"] += result["cache_hits"]
            cache_stats["misses"] += result["cache_misses"]
    for dst, parts in plan:
        with dst.open("wb") as target:
            for part in parts:
                with open(part, "rb") as source:
                    while chunk := source.read(1 << 24):
                        target.write(chunk)
                Path(part).unlink()
    totals: Counter = Counter()
    for counts in per_source.values():
        totals.update(counts)
    return {k: dict(v) for k, v in per_source.items()}, totals, dict(cache_stats)


def run_redact(
    in_dir: Path, out_dir: Path, config: RedactorConfig, jobs: int = 1
) -> dict[str, Any]:
    """Redact every <source>.jsonl in in_dir into out_dir.

    Returns a report dict with per-source counts and config snapshot.
    ``jobs`` > 1 redacts byte ranges in worker processes.

    Optional layers (each gated on its config flag, in upstream's order):
    privacy_filter -> gitleaks_fix -> llm_residue -> gitleaks (final scan).
    """
    if config.placeholder_key is None:
        # Workers must share one key even when it is the random per-process default.
        config = replace(config, placeholder_key=_key_bytes(None).decode())
    pipeline = RedactionPipeline(config)
    per_source: dict[str, dict[str, int]] = {}
    if jobs > 1:
        per_source, totals, cache_stats = _redact_parallel(in_dir, out_dir, config, jobs)
        pipeline.book.counts.update(totals)
        if pipeline.cache is not None:
            cache_stats["absorbed"] = pipeline.cache.absorb_shards()
    else:
        for src in sorted(in_dir.glob("*.jsonl")):
            per_source[src.stem] = pipeline.redact_file(src, out_dir / src.name)
        cache = pipeline.cache
        cache_stats = {"hits": cache.hits, "misses": cache.misses} if cache else {}

    layers: dict[str, Any] = {}

    if config.privacy_filter:
        # Lazy import: transformers/torch stay out of the core import path.
        from traces.redact.privacy_filter import (
            PrivacyFilter,
            privacy_filter_pass,
        )
        pf = PrivacyFilter(device=config.privacy_filter_device)
        layers["privacy_filter"] = privacy_filter_pass(
            out_dir, pf, config.privacy_filter_batch_size
        )

    if config.gitleaks_fix:
        from traces.redact.gitleaks import scrub_findings
        layers["gitleaks_fix"] = scrub_findings(out_dir)

    if config.llm_residue_url:
        from traces.redact.llm_residue import llm_residue_pass
        layers["llm_residue"] = llm_residue_pass(
            out_dir, base_url=config.llm_residue_url
        )

    if config.gitleaks:
        from traces.redact.gitleaks import run_scan
        layers["gitleaks"] = run_scan(out_dir)

    report: dict[str, Any] = {
        "counts": dict(pipeline.book.counts),
        "per_source": per_source,
        "cache": {**cache_stats, "file": str(pipeline.cache.path)} if pipeline.cache else None,
        "rules_fingerprint": pipeline.fingerprint,
        "layers": layers,
        "config": {
            "allow_public_urls": config.allow_public_urls,
            "allowed_domains": config.allowed_domains,
            "private_domains": config.private_domains,
            "privacy_filter": config.privacy_filter,
            "gitleaks": config.gitleaks,
            "gitleaks_fix": config.gitleaks_fix,
            "llm_residue": bool(config.llm_residue_url),
        },
    }
    # Write report alongside the redacted directory (sibling of out_dir).
    report_path = out_dir.parent / "REDACTION_REPORT.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
