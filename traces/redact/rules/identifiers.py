"""Identifier / PII redaction rules.

Patterns ported verbatim from RodriMora/agent-trace-redaction-methodology
`scripts/export_redacted_traces.py` Redactor.__init__ (the iban, location/GPS,
street_address, ipv6/ipv4, mac_address, bluetooth/audio hardware, contextual_phone,
email, user_at_*, private_domain, private_term*, and redacted_literal entries).

The dynamic patterns (private_domain, private_term, private_term_compound,
private_term_domain) are built from `ctx` following upstream's exact
re.escape + word-boundary logic (upstream lines 240–260, 378–397).
"""
from __future__ import annotations

import re
from pathlib import Path

from traces.redact.rules._types import Rule, RuleContext


def build(ctx: RuleContext) -> list[Rule]:
    user = re.escape(ctx.user_name)
    rules: list[Rule] = [
        ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[PII:IBAN]"),
        (
            "quicktime_location_iso6709",
            re.compile(r"(?i)(com\.apple\.quicktime\.location\.ISO6709\s*:\s*)[+-]\d{2,3}\.\d{3,}[+-]\d{2,3}\.\d{3,}(?:[+-]\d+(?:\.\d+)?)?/"),
            r"\1[PII:GPS_COORDINATES]",
        ),
        (
            "quicktime_location_accuracy",
            re.compile(r"(?i)(com\.apple\.quicktime\.location\.accuracy\.horizontal\s*:\s*)\d+(?:\.\d+)?"),
            r"\1[PII:LOCATION_ACCURACY]",
        ),
        (
            "iso6709_coordinates",
            re.compile(r"(?<![A-Za-z0-9_.])[+-]\d{2,3}\.\d{3,}[+-]\d{2,3}\.\d{3,}(?:[+-]\d+(?:\.\d+)?)?/"),
            "[PII:GPS_COORDINATES]",
        ),
        ("latlon_coordinates", re.compile(r"\b-?\d{1,2}\.\d{5,}\s*,\s*-?\d{1,3}\.\d{5,}\b"), "[PII:GPS_COORDINATES]"),
        (
            "street_address",
            re.compile(r"\b\d{1,6}\s+(?:[A-Z][A-Za-z0-9.'-]+\s+){1,5}(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Court|Ct\.?|Place|Pl\.?)\b"),
            "[PII:STREET_ADDRESS]",
        ),
        ("ipv6", re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b"), "[PII:IPV6_ADDRESS]"),
        ("mac_address", re.compile(r"\b[0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){4}\b"), "[HW:MAC_ADDRESS]"),
        ("bluetooth_path", re.compile(r"/org/bluez/[^\s\"<>`)}\]]+|dev_[0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5}"), "[HW:BLUETOOTH_PATH]"),
        ("airpods_pro", re.compile(r"(?i)\bAirPods\s+Pro\b"), "[HW:BLUETOOTH_DEVICE]"),
        ("scarlett_solo", re.compile(r"(?i)\bScarlett\s+Solo\b"), "[HW:AUDIO_DEVICE]"),
        ("focusrite_device", re.compile(r"(?i)\bFocusrite(?:[_\s-]+[A-Za-z0-9]+)*\b"), "[HW:AUDIO_DEVICE]"),
        (
            "contextual_phone",
            re.compile(r"(?i)\b((?:phone|tel|mobile|call me|telefono|tel[eé]fono)\s*[:=]?\s*)(?:\+?\d[\d .()/-]{7,}\d)"),
            r"\1[PII:PHONE]",
        ),
        ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), None),
        ("user_at_host", re.compile(user + r"@[A-Za-z0-9._-]+\b"), "[PII:USER_AT_HOST]"),
        ("user_at_literal", re.compile(user + r"@"), "[PII:USER_AT]"),
        ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[PII:IP_ADDRESS]"),
        ("redacted_literal", re.compile(r"\bREDACTED\b"), "[OMITTED]"),
    ]

    # --- Dynamic patterns (upstream lines 240–260, 378–397) ---
    private_domains = ctx.private_domains or []
    if private_domains:
        domain_pattern = r"(?i)\b(?:[A-Za-z0-9-]+\.)*(?:" + "|".join(
            re.escape(domain) for domain in private_domains
        ) + r")\b"
        rules.append(("private_domain", re.compile(domain_pattern), "[PRIVATE_DOMAIN]"))

    private_terms = ctx.private_terms or []
    if private_terms:
        escaped_terms = [re.escape(term) for term in private_terms if len(term) >= 3]
        home_name = Path(str(ctx.home_dir)).name
        identity_seed = {ctx.user_name.lower(), home_name.lower()}
        embedded_terms = [
            re.escape(term)
            for term in private_terms
            if len(term) >= 4
            and any(seed and (seed in term.lower() or term.lower() in seed) for seed in identity_seed)
        ]
        if escaped_terms:
            term_pattern = r"(?i)(?<![A-Za-z0-9_])(?:" + "|".join(escaped_terms) + r")(?![A-Za-z0-9_])"
            rules.append(("private_term", re.compile(term_pattern), "[PRIVATE_TERM]"))
        if embedded_terms:
            compound_term_pattern = (
                r"(?i)\b[A-Za-z0-9_.-]*(?:" + "|".join(embedded_terms) + r")[A-Za-z0-9_.-]*\b"
            )
            rules.append(
                ("private_term_compound", re.compile(compound_term_pattern), "[PRIVATE_TERM]")
            )
            term_domain_pattern = (
                r"(?i)\b(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]*(?:"
                + "|".join(embedded_terms)
                + r")[A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+\b"
            )
            rules.append(
                ("private_term_domain", re.compile(term_domain_pattern), "[PRIVATE_DOMAIN]")
            )

    return rules
