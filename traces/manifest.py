"""MANIFEST.json: provenance, counts, hashes."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from traces import __version__
from traces.export.metadata import SCHEMA_VERSION


def file_hashes(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "rows": sum(1 for _ in path.open())}


def write_manifest(
    out_root: Path,
    extract_counts: dict[str, int],
    redact_report: dict[str, Any],
    export_summaries: dict[str, dict],
    config_snapshot: dict[str, Any],
) -> Path:
    # Per-source extraction/redaction data stays keyed by extractor name.
    # Export attribution is recorded separately because adapters can expose
    # source variants such as ``gemini-cli`` and ``opencode-desktop``.
    sources: dict[str, Any] = {}
    for src, n in extract_counts.items():
        sources[src] = {
            "raw_records": n,
            "redaction_counts": redact_report.get("per_source", {}).get(src, {}),
        }
    files: dict[str, Any] = {}
    export_dir = out_root / "export"
    for name in (
        "messages.jsonl",
        "sharegpt.jsonl",
        "execution.jsonl",
        "assistant_steps.jsonl",
    ):
        p = export_dir / name
        if p.exists():
            files[f"export/{name}"] = file_hashes(p)
    export_summary = {
        "messages_rows": export_summaries.get("messages", {}).get("rows", 0),
        "messages_dropped_no_assistant": export_summaries.get("messages", {}).get("dropped_no_assistant", 0),
        "sharegpt_pairs": export_summaries.get("sharegpt", {}).get("pairs", 0),
        "sharegpt_dropped_trailing_user": export_summaries.get("sharegpt", {}).get("dropped_trailing_user", 0),
    }
    messages_by_source = export_summaries.get("messages", {}).get("by_source", {})
    sharegpt_by_source = export_summaries.get("sharegpt", {}).get("by_source", {})
    if messages_by_source:
        export_summary["messages_by_source"] = messages_by_source
    if sharegpt_by_source:
        export_summary["sharegpt_pairs_by_source"] = sharegpt_by_source
    if "execution" in export_summaries:
        export_summary["execution_rows"] = export_summaries["execution"].get(
            "rows", 0
        )
        export_summary["execution_by_source"] = export_summaries[
            "execution"
        ].get("by_source", {})
    if "steps" in export_summaries:
        export_summary["assistant_step_rows"] = export_summaries["steps"].get(
            "rows", 0
        )
        export_summary["assistant_targets"] = export_summaries["steps"].get(
            "targets", 0
        )
        export_summary["assistant_steps_by_source"] = export_summaries[
            "steps"
        ].get("by_source", {})

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tool_version": __version__,
        "metadata_schema_version": SCHEMA_VERSION,
        "config": config_snapshot,
        "sources": sources,
        "export_summary": export_summary,
        "files": files,
    }
    path = out_root / "MANIFEST.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return path
