"""Extractor registry and orchestration."""
from __future__ import annotations

import json
from pathlib import Path

from traces.extract.base import Conversation, Extractor
from traces.extract.claude_code import ClaudeCodeExtractor
from traces.extract.codex import CodexExtractor
from traces.extract.continue_ import ContinueExtractor
from traces.extract.cursor import CursorExtractor
from traces.extract.gemini import GeminiExtractor
from traces.extract.grok_build import GrokBuildExtractor
from traces.extract.kimi_code import KimiCodeExtractor
from traces.extract.omp import OmpExtractor
from traces.extract.opencode import OpenCodeExtractor
from traces.extract.trae import TraeExtractor
from traces.extract.windsurf import WindsurfExtractor

EXTRACTORS: dict[str, type[Extractor]] = {
    "claude_code": ClaudeCodeExtractor,
    "codex": CodexExtractor,
    "continue": ContinueExtractor,
    "cursor": CursorExtractor,
    "gemini": GeminiExtractor,
    "grok_build": GrokBuildExtractor,
    "kimi_code": KimiCodeExtractor,
    "omp": OmpExtractor,
    "opencode": OpenCodeExtractor,
    "trae": TraeExtractor,
    "windsurf": WindsurfExtractor,
}


def available_sources() -> list[str]:
    return sorted(EXTRACTORS.keys())


def _write_jsonl(path: Path, records: list[Conversation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_extract(
    sources: list[str] | None,
    root: Path,
    out_dir: Path,
) -> dict[str, int]:
    """Run the requested extractors and write one JSONL per source.

    Returns {source: record_count}. Sources with no installations or no
    conversations contribute 0 and write an empty file (so downstream
    stages always find the file).
    """
    selected = sources or available_sources()
    raw_dir = out_dir / "raw_extracted"
    counts: dict[str, int] = {}

    raw_dir.mkdir(parents=True, exist_ok=True)
    for name in selected:
        if name not in EXTRACTORS:
            raise ValueError(f"unknown source: {name!r}. available: {available_sources()}")
        extractor = EXTRACTORS[name](root)
        n = 0
        with (raw_dir / f"{name}.jsonl").open("w") as f:
            for install in extractor.find_installations():
                for rec in extractor.extract(install):
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
        counts[name] = n

    return counts
