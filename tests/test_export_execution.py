from __future__ import annotations

import json

from tests.conftest import write_jsonl
from traces.config import ExportConfig
from traces.execution import (
    execution_summary,
    normalize_execution_messages,
    split_execution_tasks,
)
from traces.export.execution import export_assistant_steps, export_execution


def _conversation() -> dict:
    return {
        "source": "test-agent",
        "session_id": "session-1",
        "messages": [
            {"role": "user", "content": "run it"},
            {
                "role": "assistant",
                "content": "done",
                "tool_calls": [{
                    "id": "call-1",
                    "name": "shell",
                    "input": {"command": "pytest -q"},
                }],
                "tool_results": [{
                    "tool_call_id": "call-1",
                    "tool": "shell",
                    "output": "passed",
                    "status": "completed",
                    "metadata": {"exit": 0},
                }],
                "diffs": [{"path": "example.py", "kind": "update"}],
            },
        ],
    }


def test_normalize_splits_snapshot_into_causal_messages():
    messages = normalize_execution_messages(_conversation())

    assert [message["role"] for message in messages] == [
        "user", "assistant", "tool", "assistant"
    ]
    assert messages[1]["content"] == ""
    assert messages[1]["tool_calls"][0]["arguments"] == {
        "command": "pytest -q"
    }
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "shell",
        "content": "passed",
        "status": "completed",
        "metadata": {"exit": 0},
        "artifacts": [{"path": "example.py", "kind": "update"}],
    }
    assert messages[3] == {"role": "assistant", "content": "done"}
    assert execution_summary(_conversation()) == {
        "tool_calls": 1,
        "tool_results": 1,
        "matched_tool_calls": 1,
        "dangling_tool_calls": 0,
        "orphan_tool_results": 0,
        "tools_complete": True,
        "final_assistant": True,
        "assistant_steps": 2,
    }


def test_execution_and_steps_exports_keep_tool_output_out_of_targets(tmp_path):
    input_dir = tmp_path / "redacted"
    write_jsonl(input_dir / "agent.jsonl", [_conversation()])
    execution_file = tmp_path / "export" / "execution.jsonl"
    steps_file = tmp_path / "export" / "assistant_steps.jsonl"
    config = ExportConfig(
        variant="all",
        require_complete_tools=True,
        require_final_assistant=True,
    )

    execution_result = export_execution(input_dir, execution_file, config)
    steps_result = export_assistant_steps(input_dir, steps_file, config)

    assert execution_result["rows"] == 1
    assert steps_result["rows"] == 1
    assert steps_result["targets"] == 2
    execution_row = json.loads(execution_file.read_text())
    assert execution_row["schema_version"] == "execution.v1"
    step_row = json.loads(steps_file.read_text())
    assert step_row["schema_version"] == "assistant-steps.v1"
    assert step_row["execution_id"] == execution_row["execution_id"]
    assert [step["message_index"] for step in step_row["assistant_steps"]] == [
        1, 3
    ]
    first_index = step_row["assistant_steps"][0]["message_index"]
    second_index = step_row["assistant_steps"][1]["message_index"]
    assert execution_row["messages"][first_index]["tool_calls"][0]["name"] == "shell"
    assert "passed" not in json.dumps(execution_row["messages"][first_index])
    assert execution_row["messages"][second_index] == {
        "role": "assistant", "content": "done"
    }
    assert execution_row["messages"][:second_index][-1]["content"] == "passed"
    assert "messages" not in step_row


def test_reasoning_phase_is_not_an_assistant_target():
    conversation = _conversation()
    conversation["messages"].insert(1, {
        "role": "assistant",
        "content": "private reasoning",
        "phase": "reasoning",
    })

    messages = normalize_execution_messages(conversation)

    assert "private reasoning" not in json.dumps(messages)


def test_task_windows_reset_at_user_turn_and_keep_system_context():
    conversation = {
        "messages": [
            {"role": "system", "content": "tool contract"},
            {"role": "user", "content": "first task"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second task"},
            {"role": "assistant", "content": "second answer"},
        ]
    }

    tasks = split_execution_tasks(conversation)

    assert len(tasks) == 2
    assert [message["content"] for message in tasks[0]] == [
        "tool contract", "first task", "first answer"
    ]
    assert [message["content"] for message in tasks[1]] == [
        "tool contract", "second task", "second answer"
    ]
