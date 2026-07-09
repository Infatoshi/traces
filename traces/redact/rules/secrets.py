"""Secrets redaction rules.

Patterns ported verbatim from RodriMora/agent-trace-redaction-methodology
`scripts/export_redacted_traces.py` Redactor.__init__ (the private_key,
authorization_bearer, bearer_token, env_secret_*, openai_key, short_sk_key,
anthropic_key, *_token, *_key, jwt, password_hash, ssh_public_key, url_basic_auth,
database_url, api_key_json_field, api_key_assignment entries).
"""
from __future__ import annotations

import re

from traces.redact.rules._types import Rule, RuleContext


def build(ctx: RuleContext) -> list[Rule]:
    return [
        (
            "private_key",
            re.compile(
                r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
                re.DOTALL,
            ),
            "[SECRET:PRIVATE_KEY]",
        ),
        (
            "authorization_bearer",
            re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)(?:REDACTED|[A-Za-z0-9._~+/=-]{8,})"),
            r"\1[SECRET:BEARER_TOKEN]",
        ),
        (
            "bearer_token",
            re.compile(r"(?i)\b(Bearer\s+)(?!\[SECRET:)(?:REDACTED|[A-Za-z0-9._~+/=-]{8,})"),
            r"\1[SECRET:BEARER_TOKEN]",
        ),
        (
            "env_secret_assignment",
            re.compile(
                r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|CREDS[_-]?(?:KEY|IV))[A-Z0-9_]*\s*=\s*)(['\"]?)(?:REDACTED|[^\s'\"\\]{8,})\2"
            ),
            r"\1[SECRET:ENV_VALUE]",
        ),
        (
            "env_secret_default",
            re.compile(
                r"(?i)([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|CREDS[_-]?(?:KEY|IV))[A-Z0-9_]*\s*:\s*\$\{[^}:]+:-)([^}\s]{3,})"
            ),
            r"\1[SECRET:ENV_DEFAULT]",
        ),
        (
            "env_secret_colon_default",
            re.compile(
                r"(?i)(\b[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|CREDS[_-]?(?:KEY|IV))[A-Z0-9_]*\s*:-)(?!\[SECRET:|\[REDACTED:)[^\s'\"\\},]{3,}"
            ),
            r"\1[SECRET:ENV_DEFAULT]",
        ),
        ("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"), "[SECRET:OPENAI_API_KEY]"),
        ("short_sk_key", re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"), "[SECRET:API_KEY_FIELD]"),
        ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "[SECRET:ANTHROPIC_API_KEY]"),
        ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[SECRET:GITHUB_TOKEN]"),
        ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "[SECRET:HUGGINGFACE_TOKEN]"),
        ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "[SECRET:GOOGLE_API_KEY]"),
        ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b"), "[SECRET:AWS_ACCESS_KEY]"),
        ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[SECRET:JWT]"),
        (
            "password_hash",
            re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}|\$(?:argon2id|argon2i|argon2d|5|6)\$[^\s'\"\\]{20,}"),
            "[SECRET:PASSWORD_HASH]",
        ),
        (
            "ssh_public_key",
            re.compile(r"\bssh-(?:rsa|ed25519)\s+[A-Za-z0-9+/=]{40,}(?:\s+[^\s'\"<>`]+)?"),
            "[SECRET:SSH_PUBLIC_KEY]",
        ),
        (
            "url_basic_auth",
            re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s:@]+@"),
            r"\1[SECRET:BASIC_AUTH]@",
        ),
        (
            "database_url",
            re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s'\"<>`]+", re.I),
            "[SECRET:DATABASE_URL]",
        ),
        (
            "api_key_json_field",
            re.compile(r'(?i)((?:\\?")api[_-]?key(?:\\?")\s*:\s*(?:\\?"))(?!\[SECRET:|\{env:)[^\\"]+((?:\\?"))'),
            r"\1[SECRET:API_KEY_FIELD]\2",
        ),
        (
            "api_key_assignment",
            re.compile(r"(?i)\b(api[_-]?key|apikey)\s*=\s*(['\"]?)(?!\[SECRET:|\{env:)[^\s'\"\\]{3,}\2"),
            r"\1=[SECRET:API_KEY_FIELD]",
        ),
    ]
