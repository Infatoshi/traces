"""Shared rule types, isolated to avoid import cycles.

``RuleContext`` and the ``Rule`` type alias live here so the family modules
(``paths``, ``secrets``, ``urls``, ``identifiers``) can import them without
triggering the package ``__init__`` (which imports the family modules back).
``rules/__init__.py`` re-exports both names for the public API.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from traces.redact.rules.constants import DEFAULT_PUBLIC_URL_ALLOWLIST

Rule = tuple[str, "re.Pattern[str]", str | None]


@dataclass
class RuleContext:
    user_name: str
    home_dir: str
    private_terms: list[str] = field(default_factory=list)
    private_domains: list[str] = field(default_factory=list)
    allow_public_urls: bool = False
    allowed_domains: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.allowed_domains = set(self.allowed_domains or set())
        if self.allow_public_urls:
            self.allowed_domains.update(DEFAULT_PUBLIC_URL_ALLOWLIST)
        self.allowed_domains = {
            d.lower().lstrip(".") for d in self.allowed_domains
        }
