from __future__ import annotations

from traces.publish.dataset_card import render_card


def test_card_has_yaml_frontmatter():
    manifest = {
        "sources": {"claude_code": {"raw_records": 7}},
        "export_summary": {"messages_rows": 10, "sharegpt_pairs": 22},
        "config": {"gitleaks": True},
    }
    card = render_card(manifest, repo_id="user/my-traces")
    assert card.startswith("---\n")
    assert "license: mit" in card
    assert "user/my-traces" in card
    assert "claude_code" in card
    # Per-source raw record count is rendered in the source table.
    assert "| 7 |" in card
    # Totals come from export_summary, not summed per-source fields.
    assert "10 conversations" in card
    assert "22 instruction pairs" in card


def test_card_uses_export_summary_not_per_source_export_fields():
    # Regression: the card used to sum per-source `exported_messages` /
    # `exported_sharegpt_pairs`. Those fields no longer exist; totals must come
    # from the global `export_summary` block.
    manifest = {
        "sources": {
            "claude_code": {"raw_records": 5},
            "cursor": {"raw_records": 0},
        },
        "export_summary": {
            "messages_rows": 9,
            "messages_dropped_no_assistant": 1,
            "sharegpt_pairs": 8,
            "sharegpt_dropped_trailing_user": 0,
        },
        "config": {},
    }
    card = render_card(manifest, repo_id="user/traces")
    # Totals reflect export_summary, NOT a sum of per-source raw_records (5)
    # and NOT the old per-source export fields.
    assert "9 conversations" in card
    assert "8 instruction pairs" in card
    # The dropped per-source export columns are gone: header has one count col.
    assert "Raw records" in card
    assert "ShareGPT pairs |" not in card  # old per-source column header
    assert "Conversations |" not in card   # old per-source column header


def test_card_repo_url_is_resmp_dev():
    manifest = {"sources": {}, "export_summary": {}, "config": {}}
    card = render_card(manifest, repo_id="user/x")
    assert "https://github.com/Infatoshi/traces" in card
    assert "your-org" not in card
    assert "kearm/agent-trace-share" not in card
