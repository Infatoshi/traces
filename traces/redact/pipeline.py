"""Orchestrates the layered redaction pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from traces.config import RedactorConfig
from traces.redact.discovery import discover_private_terms
from traces.redact.rules import RuleContext, make_redactor
from traces.redact.rules.placeholders import PlaceholderBook
from traces.redact.schema import redact_obj


class RedactionPipeline:
    def __init__(self, config: RedactorConfig) -> None:
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
        self.book = PlaceholderBook()
        self._redact_str = make_redactor(ctx, self.book)

    def redact_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return redact_obj(record, self._redact_str, self.book)

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
        delta: dict[str, int] = {}
        for k, v in self.book.counts.items():
            d = v - before.get(k, 0)
            if d:
                delta[k] = d
        return delta


def run_redact(in_dir: Path, out_dir: Path, config: RedactorConfig) -> dict[str, Any]:
    """Redact every <source>.jsonl in in_dir into out_dir.

    Returns a report dict with per-source counts and config snapshot.

    Optional layers (each gated on its config flag, in upstream's order):
    privacy_filter -> gitleaks_fix -> llm_residue -> gitleaks (final scan).
    """
    pipeline = RedactionPipeline(config)
    per_source: dict[str, dict[str, int]] = {}
    for src in sorted(in_dir.glob("*.jsonl")):
        per_source[src.stem] = pipeline.redact_file(src, out_dir / src.name)

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
