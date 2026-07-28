from __future__ import annotations

import pytest

from tests.conftest import make_conversation, make_message
from traces.config import ExportConfig
from traces.export.filters import filter_reason


def rich_conversation() -> dict:
    return make_conversation(
        source="opencode-cli",
        project_path="/work/alpha",
        status="completed",
        reasoning_effort="high",
        session_kind="subagent",
        created_at="2026-07-10T12:00:00Z",
        messages=[
            make_message("user", "do it"),
            make_message(
                "assistant",
                "done",
                model="model-a",
                tool_calls=[{"name": "edit"}],
                diffs=[{"file": "a.py"}],
            ),
        ],
    )


@pytest.mark.parametrize(
    ("config", "reason"),
    [
        (ExportConfig(include_sources=["codex"]), "source_not_included"),
        (ExportConfig(drop_sources=["opencode-*"]), "source_dropped"),
        (ExportConfig(projects=["*/beta"]), "project_not_included"),
        (ExportConfig(models=["model-b"]), "model_not_included"),
        (ExportConfig(statuses=["aborted"]), "status_not_included"),
        (ExportConfig(reasoning_efforts=["low"]), "reasoning_effort_not_included"),
        (ExportConfig(session_kinds=["primary"]), "session_kind_not_included"),
        (ExportConfig(since="2026-07-11T00:00:00Z"), "before_since"),
        (ExportConfig(until="2026-07-09T00:00:00Z"), "after_until"),
        (ExportConfig(min_turns=3), "below_min_messages"),
        (ExportConfig(max_turns=1), "above_max_messages"),
    ],
)
def test_filters_report_auditable_reasons(config: ExportConfig, reason: str) -> None:
    assert filter_reason(rich_conversation(), config) == reason


def test_tool_and_diff_filters_use_normalized_message_fields() -> None:
    assert filter_reason(rich_conversation(), ExportConfig(require_tools=True)) is None
    assert filter_reason(rich_conversation(), ExportConfig(require_diffs=True)) is None
    plain = make_conversation()
    assert filter_reason(plain, ExportConfig(require_tools=True)) == "tools_required"
    assert filter_reason(plain, ExportConfig(require_diffs=True)) == "diffs_required"


def test_observed_session_category_filters_accept_globs() -> None:
    config = ExportConfig(reasoning_efforts=["h*"], session_kinds=["sub*"])
    assert filter_reason(rich_conversation(), config) is None


def test_invalid_time_filter_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid --since"):
        filter_reason(rich_conversation(), ExportConfig(since="not-a-time"))
