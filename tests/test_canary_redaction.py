"""End-to-end canary: synthetic PII must not survive the full pipeline."""
from __future__ import annotations

from tests.conftest import (
    CANARY_BEARER,
    CANARY_EMAIL,
    CANARY_GITHUB_TOKEN,
    CANARY_HOME_PATH,
    CANARY_OPENAI_KEY,
    CANARY_PHONE,
    CANARY_PRIVATE_URL,
)

ALL_CANARIES = [
    CANARY_OPENAI_KEY, CANARY_GITHUB_TOKEN, CANARY_EMAIL,
    CANARY_HOME_PATH, CANARY_PRIVATE_URL, CANARY_PHONE, CANARY_BEARER,
]


def test_no_canary_survives_redaction(tmp_path, canary_conversation):
    """The redacted output must not contain any canary value."""
    from tests.conftest import write_jsonl
    in_dir = tmp_path / "raw_extracted"
    out_dir = tmp_path / "redacted"
    write_jsonl(in_dir / "claude_code.jsonl", [canary_conversation])

    from traces.config import RedactorConfig
    from traces.redact.pipeline import run_redact
    run_redact(in_dir, out_dir, RedactorConfig(
        user_name="alexmorgan", home_dir="/Users/alexmorgan",
    ))

    raw = (out_dir / "claude_code.jsonl").read_text()
    for canary in [
        *ALL_CANARIES,
        "alexmorgan",  # auto-discovered username/home_dir
    ]:
        assert canary not in raw, f"canary survived redaction: {canary!r}"


def test_no_canary_survives_export(tmp_path, canary_conversation):
    """After export, neither variant contains canaries."""
    from tests.conftest import write_jsonl
    in_dir = tmp_path / "raw_extracted"
    red_dir = tmp_path / "redacted"
    exp_dir = tmp_path / "export"
    write_jsonl(in_dir / "claude_code.jsonl", [canary_conversation])

    from traces.config import ExportConfig, RedactorConfig
    from traces.export.messages import export_messages
    from traces.export.sharegpt import export_sharegpt
    from traces.redact.pipeline import run_redact

    run_redact(in_dir, red_dir, RedactorConfig(user_name="alexmorgan", home_dir="/Users/alexmorgan"))
    export_messages(red_dir, exp_dir / "messages.jsonl", ExportConfig(variant="messages"))
    export_sharegpt(red_dir, exp_dir / "sharegpt.jsonl", ExportConfig(variant="sharegpt"))

    for f in [exp_dir / "messages.jsonl", exp_dir / "sharegpt.jsonl"]:
        text = f.read_text()
        for canary in ALL_CANARIES:
            assert canary not in text, f"canary survived export in {f.name}: {canary!r}"
