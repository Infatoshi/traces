from __future__ import annotations

import json

from traces.extract.omp import OmpExtractor, extract_omp_session


def test_extract_omp_native_messages(tmp_path):
    path = tmp_path / ".omp" / "agent" / "sessions" / "project" / "session.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "type": "session", "version": 3, "id": "session-1",
            "timestamp": "2026-08-01T00:00:00Z", "cwd": "/tmp/project",
            "title": "Task", "model": "openai/gpt-5.6-sol",
        },
        {"type": "thinking_level_change", "thinkingLevel": "high", "timestamp": "2026-08-01T00:00:01Z"},
        {
            "type": "message", "id": "user-1", "parentId": None,
            "timestamp": "2026-08-01T00:00:02Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "run tests"}]},
        },
        {
            "type": "message", "id": "assistant-1", "parentId": "user-1",
            "timestamp": "2026-08-01T00:00:03Z",
            "message": {
                "role": "assistant", "model": "openai/gpt-5.6-sol",
                "content": [
                    {"type": "thinking", "thinking": "inspect"},
                    {"type": "text", "text": "Running tests"},
                    {"type": "toolCall", "id": "call-1", "name": "bash", "arguments": {"command": "pytest -q"}},
                ],
                "usage": {"input": 10, "output": 5, "cacheRead": 20, "cacheWrite": 1},
                "stopReason": "toolUse",
            },
        },
        {
            "type": "message", "id": "tool-1", "parentId": "assistant-1",
            "timestamp": "2026-08-01T00:00:04Z",
            "message": {
                "role": "toolResult", "toolCallId": "call-1", "toolName": "bash",
                "content": [{"type": "text", "text": "1 passed"}],
                "details": {"exitCode": 0}, "isError": False,
            },
        },
        {
            "type": "custom_message",
            "customType": "async-result",
            "content": "Subagent finished.",
            "id": "custom-1",
            "parentId": "tool-1",
            "timestamp": "2026-08-01T00:00:05Z",
        },
        {
            "type": "custom", "customType": "session_exit",
            "data": {"reason": "completed"}, "timestamp": "2026-08-01T00:00:06Z",
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    conversation = extract_omp_session(path)

    assert conversation is not None
    assert conversation["source"] == "omp"
    assert conversation["session_id"] == "session-1"
    assert conversation["project_path"] == "/tmp/project"
    assert conversation["reasoning_effort"] == "high"
    assert conversation["status"] == "completed"
    assert conversation["tool_schema_quality"] == "missing_not_recorded"
    assistant = conversation["messages"][1]
    assert assistant["reasoning"] == "inspect"
    assert assistant["content"] == "Running tests"
    assert assistant["tool_calls"] == [{
        "id": "call-1", "name": "bash", "input": {"command": "pytest -q"}, "kind": "function",
    }]
    result = conversation["messages"][2]["tool_result"]
    assert result["status"] == "success"
    assert result["details"] == {"exitCode": 0}
    injection = conversation["messages"][3]
    assert injection["role"] == "user"
    assert injection["phase"] == "injection"
    assert injection["origin_kind"] == "async-result"
    assert conversation["token_usage"] == {
        "input": 10, "output": 5, "cached_input": 20, "cache_write": 1, "total": 36,
    }


def test_find_omp_installation(tmp_path):
    (tmp_path / ".omp" / "agent" / "sessions").mkdir(parents=True)
    assert OmpExtractor(tmp_path).find_installations() == [tmp_path / ".omp"]
