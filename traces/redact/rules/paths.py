"""Path-related redaction rules.

Patterns ported verbatim from RodriMora/agent-trace-redaction-methodology
`scripts/export_redacted_traces.py` Redactor.__init__ (the pi_encoded_path_component,
encoded_home_path, encoded_home_path_literal, generic_home_path, home_path,
mac_home_path, ssh_remote, and private_user_name entries).
"""
from __future__ import annotations

import re

from traces.redact.rules._types import Rule, RuleContext


def build(ctx: RuleContext) -> list[Rule]:
    user = re.escape(ctx.user_name)
    home = re.escape(str(ctx.home_dir))
    encoded_home = "--" + re.escape(str(ctx.home_dir).strip("/").replace("/", "-"))

    return [
        (
            "pi_encoded_path_component",
            re.compile(r"(?<![A-Za-z0-9_-])--(?=[A-Za-z0-9_.-]*[A-Za-z0-9])[A-Za-z0-9_.-]{3,}--(?![A-Za-z0-9_-])"),
            None,
        ),
        ("encoded_home_path", re.compile(encoded_home + r"(?:-[A-Za-z0-9_.]+)*--"), None),
        ("encoded_home_path_literal", re.compile(encoded_home), "[ENCODED_HOME_PATH]"),
        ("generic_home_path", re.compile(r"(?:/home|home)/[A-Za-z0-9._-]+(?:/[^\s'\"<>`)}\]]*)?"), None),
        ("home_path", re.compile(r"(?:" + home + r"|~)(?:/[^\s'\"<>`)}\]]*)?"), None),
        ("mac_home_path", re.compile(r"/Users/[A-Za-z0-9._-]+(?:/[^\s'\"<>`)}\]]*)?"), None),
        (
            "ssh_remote",
            re.compile(r"\b(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+:(?:/)?[A-Za-z0-9._/-]+\.git\b"),
            None,
        ),
        ("private_user_name", re.compile(r"(?i)\b" + user + r"\b"), "[PRIVATE_USER]"),
    ]
