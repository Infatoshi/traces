"""Rule composition. Order-sensitive: composes in upstream's interleaved order.

Patterns are split into family modules (paths, secrets, urls, identifiers) but
``build_patterns`` re-interleaves them in the exact order upstream's
``Redactor.patterns`` list uses. Order matters: high-specificity patterns
(e.g. ``openai_key``) must run before low-specificity ones (e.g. ``url``).
"""
from __future__ import annotations

import re

from traces.fastmatch import Prefilter
from traces.redact.rules import identifiers, paths, secrets, urls
from traces.redact.rules._types import Rule, RuleContext
from traces.redact.rules.placeholders import PlaceholderBook

# Upstream order (from scripts/export_redacted_traces.py self.patterns, lines
# 261-408). The private_domain / private_term_domain / private_term /
# private_term_compound entries are interleaved here exactly as upstream emits
# them (conditionally, via starred-list splats), so dynamic patterns land in the
# right slot regardless of how many private domains/terms are configured.
UPSTREAM_ORDER = [
    "private_key",
    "pi_encoded_path_component",
    "authorization_bearer",
    "bearer_token",
    "env_secret_assignment",
    "env_secret_colon",
    "env_secret_default",
    "env_secret_colon_default",
    "openai_key",
    "short_sk_key",
    "anthropic_key",
    "github_token",
    "huggingface_token",
    "google_api_key",
    "aws_access_key",
    "jwt",
    "password_hash",
    "ssh_public_key",
    "iban",
    "quicktime_location_iso6709",
    "quicktime_location_accuracy",
    "iso6709_coordinates",
    "latlon_coordinates",
    "street_address",
    "ipv6",
    "mac_address",
    "bluetooth_path",
    "airpods_pro",
    "scarlett_solo",
    "focusrite_device",
    "url_basic_auth",
    "database_url",
    "opencode_share_url",
    "private_url",
    "url",
    "api_key_json_field",
    "api_key_assignment",
    "contextual_phone",
    "email",
    "user_at_host",
    "user_at_literal",
    "ipv4",
    "encoded_home_path",
    "encoded_home_path_literal",
    "private_domain",
    "private_term_domain",
    "private_term",
    "private_term_compound",
    "private_user_name",
    "redacted_literal",
    "generic_home_path",
    "home_path",
    "mac_home_path",
    "ssh_remote",
]

__all__ = [
    "UPSTREAM_ORDER",
    "PlaceholderBook",
    "Rule",
    "RuleContext",
    "build_patterns",
    "is_allowed_url",
    "make_redactor",
    "redact_string",
]


def build_patterns(ctx: RuleContext) -> list[Rule]:
    """Compose patterns in upstream's interleaved order.

    Upstream's ``Redactor.patterns`` list is NOT grouped by family — it
    interleaves so high-specificity patterns (e.g. private_key, openai_key) run
    before low-specificity ones (e.g. url, email). Order is preserved verbatim.
    """
    p = paths.build(ctx)
    s = secrets.build(ctx)
    u = urls.build(ctx)
    i = identifiers.build(ctx)

    # Index by category name for upstream-faithful interleaving.
    by_name: dict[str, Rule] = {}
    for family in (p, s, u, i):
        for rule in family:
            by_name[rule[0]] = rule

    # Publish the allowlist so redact_string's closure can read it without
    # rebuilding patterns. Mirrors upstream binding allowed_domains to the
    # Redactor instance.
    _ALLOWED_DOMAINS_REF[0] = ctx.allowed_domains

    return [by_name[name] for name in UPSTREAM_ORDER if name in by_name]


def is_allowed_url(value: str, allowed_domains: set[str]) -> bool:
    if not allowed_domains:
        return False
    m = re.match(r"(?i)^https?://([^/:?#]+)", value)
    if not m:
        return False
    host = m.group(1).lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allowed_domains)


# redact_string needs allowed_domains at call time. We pass it via a small
# wrapper object so the closure can read it without rebuilding patterns.
_ALLOWED_DOMAINS_REF: list[set[str]] = [set()]


def redact_string(
    value: str,
    patterns: list[Rule],
    book: PlaceholderBook,
    prefilter: Prefilter | None = None,
) -> str:
    """Apply ``patterns`` in order.

    With a ``prefilter`` built from the same patterns, rules that cannot match
    the current string are skipped. Candidates are recomputed after every
    substitution, so the output is identical to running every rule.
    """
    out = value
    candidates = prefilter.candidates(out) if prefilter is not None else None
    for index, (category, pattern, replacement) in enumerate(patterns):
        if candidates is not None and index not in candidates:
            continue

        def repl(m: re.Match[str], _cat: str = category, _rep: str | None = replacement) -> str:
            if _cat == "url" and is_allowed_url(m.group(0), _ALLOWED_DOMAINS_REF[0]):
                return m.group(0)
            book.counts[_cat] += 1
            if _rep is None:
                return book.stable(_cat, m.group(0))
            return m.expand(_rep)

        new = pattern.sub(repl, out)
        if prefilter is not None and new != out:
            candidates = prefilter.candidates(new)
        out = new
    return out


def make_redactor(ctx: RuleContext, book: PlaceholderBook):
    """Return a callable redact_string(value) bound to ctx + book."""
    patterns = build_patterns(ctx)
    prefilter = Prefilter([pattern for _name, pattern, _rep in patterns])
    _ALLOWED_DOMAINS_REF[0] = ctx.allowed_domains

    def _redact(value: str) -> str:
        return redact_string(value, patterns, book, prefilter)

    return _redact
