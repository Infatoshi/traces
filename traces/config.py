"""Configuration dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RedactorConfig:
    user_name: str | None = None        # None -> auto-discover
    home_dir: str | None = None         # None -> Path.home()
    allow_public_urls: bool = False
    allowed_domains: list[str] = field(default_factory=list)
    private_terms: list[str] = field(default_factory=list)
    private_domains: list[str] = field(default_factory=list)
    privacy_filter: bool = False
    privacy_filter_device: str = "auto"
    privacy_filter_batch_size: int = 64
    gitleaks: bool = False
    gitleaks_fix: bool = False
    llm_residue_url: str | None = None


@dataclass
class ExportConfig:
    variant: str = "both"               # "messages" | "sharegpt" | "both"
    min_turns: int | None = None
    max_turns: int | None = None
    min_assistant_chars: int | None = None
    drop_sources: list[str] = field(default_factory=list)
    dedup: bool = False
