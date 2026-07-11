from __future__ import annotations

from traces.export.flatten import flatten_message


def test_plain_content_only():
    assert flatten_message({"role": "user", "content": "hello"}) == "hello"


def test_content_plus_tool_use():
    msg = {
        "role": "assistant",
        "content": "Editing now.",
        "tool_use": {"name": "edit_file", "input": {"path": "f.py"}},
    }
    out = flatten_message(msg)
    assert out.startswith("Editing now.")
    assert '<tool_use name="edit_file">' in out
    assert '"path": "f.py"' in out
    assert out.endswith("</tool_use>")


def test_tool_uses_list_rendered_in_order():
    msg = {
        "role": "assistant",
        "content": "x",
        "tool_uses": [
            {"name": "a", "input": {}},
            {"name": "b", "input": {}},
        ],
    }
    out = flatten_message(msg)
    assert out.index("a") < out.index("b")


def test_suggested_diff_rendered():
    msg = {
        "role": "assistant",
        "content": "Here's the change.",
        "suggested_diffs": [{"file": "f.py", "diff": "-old\n+new"}],
    }
    out = flatten_message(msg)
    assert '<suggested_diff file="f.py">' in out
    assert "-old\n+new" in out


def test_unknown_field_rendered_as_generic_field():
    msg = {"role": "user", "content": "hi", "custom_meta": {"k": "v"}}
    out = flatten_message(msg)
    assert '<field name="custom_meta">' in out
    assert '"k": "v"' in out


def test_deterministic_output():
    msg = {
        "role": "assistant",
        "content": "c",
        "tool_use": {"name": "t", "input": {}},
        "tool_results": [{"type": "text", "text": "ok"}],
        "custom_field": {"z": 1, "a": 2},
    }
    a = flatten_message(msg)
    b = flatten_message(msg)
    assert a == b
    # unknown fields sorted by key
    assert a.index('"a"') < a.index('"z"')


def test_model_and_timestamp_not_in_content():
    msg = {"role": "assistant", "content": "x", "model": "claude-x", "timestamp": "t"}
    out = flatten_message(msg)
    assert "claude-x" not in out
    assert "timestamp" not in out.lower() or "timestamp" not in out


def test_message_metadata_is_not_rendered_as_training_content():
    out = flatten_message({
        "role": "assistant",
        "content": "done",
        "id": "provider-message-id",
        "parent_id": "provider-parent-id",
        "usage": {"input_tokens": 10},
        "tokens": {"output": 5},
        "stop_reason": "end_turn",
    })
    assert out == "done"
