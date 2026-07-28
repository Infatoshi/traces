from __future__ import annotations

from tests.conftest import make_conversation, make_message
from traces.export.metadata import build_metadata, trace_id

KEY = "metadata-test-key"


def test_metadata_reads_only_the_normalized_contract() -> None:
    conv = make_conversation(
        source="codex",
        session_id="session-1",
        created_at="2026-07-10T12:30:00Z",
        start_time="provider-alias-not-normalized",
        last_updated="provider-alias-not-normalized",
        duration_ms=123,
        title="private title",
        total_token_usage={"input_tokens": 999},
    )

    metadata = build_metadata(conv, KEY)

    assert metadata["source"] == "codex"
    assert metadata["session"] == {"created_at": "2026-07-10"}
    assert "duration_ms" not in str(metadata)
    assert "private title" not in str(metadata)
    assert "token_usage" not in metadata


def test_metadata_counts_the_declared_message_fields() -> None:
    conv = make_conversation(messages=[
        make_message("user", "change it"),
        make_message(
            "assistant",
            "done",
            tool_calls=[{"name": "edit"}],
            tool_results=[{"status": "completed"}],
            diffs=[{"file": "f.py"}],
        ),
    ])

    counts = build_metadata(conv, KEY)["counts"]

    assert counts == {
        "messages": 2,
        "roles": {"user": 1, "assistant": 1},
        "assistant_chars": 4,
        "tool_calls": 1,
        "tool_results": 1,
        "diffs": 1,
    }


def test_metadata_copies_only_canonical_token_totals() -> None:
    conv = make_conversation(token_usage={
        "input": 10,
        "output": 5,
        "cached_input": 3,
        "provider_private_counter": 99,
    })

    assert build_metadata(conv, KEY)["token_usage"] == {
        "input": 10,
        "output": 5,
        "cached_input": 3,
    }


def test_ids_are_keyed_opaque_and_source_namespaced() -> None:
    first = make_conversation(source="codex", session_id="same")
    second = make_conversation(source="claude_code", session_id="same")

    assert trace_id(first, KEY) == trace_id(first, KEY)
    assert trace_id(first, KEY) != trace_id(first, "other-key")
    assert trace_id(first, KEY) != trace_id(second, KEY)
    assert "same" not in trace_id(first, KEY)


def test_content_fallback_identity_includes_normalized_message_metadata() -> None:
    first = make_conversation(messages=[
        make_message("assistant", "same text", model="model-a"),
    ])
    second = make_conversation(messages=[
        make_message("assistant", "same text", model="model-b"),
    ])

    assert trace_id(first, KEY) != trace_id(second, KEY)


def test_project_metadata_is_opaque_and_real_timestamps_are_day_bucketed() -> None:
    conv = make_conversation(
        project_path="/private/client/project",
        created_at="2026-07-10T23:59:59-06:00",
    )

    metadata = build_metadata(conv, KEY)

    assert metadata["project"]["id"].startswith("tp_")
    assert "/private/client/project" not in str(metadata)
    assert metadata["session"]["created_at"] == "2026-07-11"


def test_metadata_includes_declared_session_categories() -> None:
    conv = make_conversation(
        reasoning_effort="high",
        session_kind="subagent",
        sandbox_profile="off",
    )

    assert build_metadata(conv, KEY)["session"] == {
        "reasoning_effort": "high",
        "kind": "subagent",
        "sandbox_profile": "off",
    }
