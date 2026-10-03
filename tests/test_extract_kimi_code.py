from __future__ import annotations

import json

from traces.extract.kimi_code import KimiCodeExtractor, extract_kimi_agent


def _write_lines(path, rows):
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_extract_kimi_native_execution_and_exact_schema(tmp_path):
    session = tmp_path / ".kimi-code" / "sessions" / "wd_project" / "session-1"
    agent = session / "agents" / "main"
    session.mkdir(parents=True)
    (session / "state.json").write_text(json.dumps({
        "title": "Example",
        "workDir": "/tmp/project",
        "createdAt": "2026-08-01T00:00:00Z",
        "agents": {"main": {"type": "main"}},
    }))
    schema = {
        "name": "shell",
        "description": "Run a command",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
    }
    _write_lines(agent / "wire.jsonl", [
        {"type": "llm.tools_snapshot", "hash": "tools-1", "tools": [schema], "time": 1},
        {
            "type": "context.append_message",
            "message": {"role": "user", "content": [{"type": "text", "text": "run tests"}]},
            "time": 2,
        },
        {
            "type": "llm.request",
            "model": "k3",
            "thinkingEffort": "high",
            "toolsHash": "tools-1",
            "time": 3,
        },
        {
            "type": "context.append_loop_event",
            "event": {
                "type": "content.part", "stepUuid": "step-1", "turnId": "turn-1",
                "part": {"type": "thinking", "think": "inspect first"},
            },
            "time": 4,
        },
        {
            "type": "context.append_loop_event",
            "event": {
                "type": "tool.call", "stepUuid": "step-1", "turnId": "turn-1",
                "toolCallId": "call-1", "name": "shell", "args": {"command": "pytest -q"},
            },
            "time": 5,
        },
        {
            "type": "context.append_loop_event",
            "event": {
                "type": "tool.result", "toolCallId": "call-1",
                "result": {"output": "1 passed", "isError": False},
            },
            "time": 6,
        },
        {
            "type": "context.append_loop_event",
            "event": {
                "type": "step.end", "stepUuid": "step-1", "messageId": "message-1",
                "finishReason": "tool_use",
                "usage": {"inputOther": 10, "inputCacheRead": 20, "output": 5},
            },
            "time": 7,
        },
    ])

    conversation = extract_kimi_agent(agent)

    assert conversation is not None
    assert conversation["source"] == "kimi_code"
    assert conversation["model"] == "k3"
    assert conversation["project_path"] == "/tmp/project"
    assert conversation["tool_schema_quality"] == "captured_exact"
    assert conversation["tool_snapshots"][0]["tools"] == [{
        "type": "function", "function": schema,
    }]
    assistant = conversation["messages"][1]
    assert assistant["reasoning"] == "inspect first"
    assert assistant["tool_schema_hash"] == "tools-1"
    assert assistant["tool_calls"][0]["input"] == {"command": "pytest -q"}
    result = conversation["messages"][2]["tool_result"]
    assert result["tool_call_id"] == "call-1"
    assert result["status"] == "success"
    assert conversation["token_usage"] == {
        "input": 10, "output": 5, "cached_input": 20, "cache_write": 0, "total": 35,
    }


def test_marks_non_user_origin_as_injected_context(tmp_path):
    session = tmp_path / ".kimi-code" / "sessions" / "wd_project" / "session-1"
    agent = session / "agents" / "main"
    session.mkdir(parents=True)
    (session / "state.json").write_text(json.dumps({
        "agents": {"main": {"type": "main"}},
    }))
    _write_lines(agent / "wire.jsonl", [{
        "type": "context.append_message",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "background result"}],
            "origin": {"kind": "background_task"},
        },
    }])

    conversation = extract_kimi_agent(agent)

    assert conversation is not None
    assert conversation["messages"][0]["phase"] == "injection"
    assert conversation["messages"][0]["origin_kind"] == "background_task"


def test_find_kimi_installation(tmp_path):
    (tmp_path / ".kimi-code" / "sessions").mkdir(parents=True)
    assert KimiCodeExtractor(tmp_path).find_installations() == [tmp_path / ".kimi-code"]
