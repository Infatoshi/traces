"""Tests for the Codex extractor."""
from __future__ import annotations

import json
from pathlib import Path

from traces.extract.codex import CodexExtractor


def write_rollout(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_extract_basic_user_assistant_turns(tmp_path):
    install = tmp_path / ".codex"
    session = install / "sessions" / "2024" / "01" / "01" / "rollout-abc.jsonl"
    write_rollout(session, [
        {"type": "session_meta", "payload": {"id": "abc", "cwd": "/tmp/proj",
                                              "timestamp": "2024-01-01T00:00:00Z"}},
        {"type": "event_msg", "timestamp": "t1",
         "payload": {"type": "user_message", "message": "hello"}},
        {"type": "event_msg", "timestamp": "t2",
         "payload": {"type": "agent_message", "message": "hi there",
                     "model": "gpt-5-codex"}},
    ])

    convs = list(CodexExtractor().extract(install))

    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "codex"
    assert conv["session_id"] == "abc"
    assert conv["cwd"] == "/tmp/proj"
    assert conv["timestamp"] == "2024-01-01T00:00:00Z"
    assert len(conv["messages"]) == 2
    assert conv["messages"][0] == {"role": "user", "content": "hello", "timestamp": "t1"}
    assert conv["messages"][1]["role"] == "assistant"
    assert conv["messages"][1]["content"] == "hi there"
    assert conv["messages"][1]["model"] == "gpt-5-codex"


def test_extract_tool_use_and_tool_results_collected(tmp_path):
    install = tmp_path / ".codex"
    session = install / "sessions" / "rollout-r.jsonl"
    write_rollout(session, [
        {"type": "session_meta", "payload": {"id": "r"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": "do it"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "ok"}},
        {"type": "event_msg", "payload": {"type": "tool_use", "tool": "shell",
                                          "input": {"cmd": "ls"}}},
        {"type": "event_msg", "payload": {"type": "tool_result", "tool": "shell",
                                          "output": "file.txt"}},
        {"type": "event_msg", "payload": {"type": "diff", "file": "f.py",
                                          "diff": "-old\n+new"}},
    ])

    convs = list(CodexExtractor().extract(install))
    conv = convs[0]
    # Codex collects tool_use/tool_result/diff into a top-level tool_results list
    assert len(conv["tool_results"]) == 3
    assert conv["tool_results"][0]["type"] == "tool_use"
    assert conv["tool_results"][0]["tool"] == "shell"
    assert conv["tool_results"][1]["type"] == "tool_result"
    assert conv["tool_results"][2]["type"] == "diff"
    assert conv["tool_results"][2]["file"] == "f.py"


def test_extracts_current_codex_shape_observed_in_local_rollouts(tmp_path):
    install = tmp_path / ".codex"
    session = install / "sessions" / "rollout-current.jsonl"
    write_rollout(session, [
        {"type": "session_meta", "timestamp": "2026-07-10T10:00:00Z", "payload": {
            "id": "thread-1",
            "timestamp": "2026-07-10T10:00:00Z",
            "cwd": "/work/project",
            "cli_version": "0.143.0",
        }},
        {"type": "turn_context", "timestamp": "2026-07-10T10:00:01Z", "payload": {
            "model": "gpt-model",
            "effort": "high",
            "approval_policy": "never",
            "sandbox_policy": {"type": "danger-full-access"},
        }},
        {"type": "event_msg", "timestamp": "2026-07-10T10:00:02Z", "payload": {
            "type": "user_message", "message": "hello", "images": [],
        }},
        {"type": "response_item", "timestamp": "2026-07-10T10:00:02Z", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "hello"}],
        }},
        {"type": "response_item", "timestamp": "2026-07-10T10:00:03Z", "payload": {
            "type": "function_call", "name": "exec_command", "call_id": "call-1",
            "arguments": "{}",
        }},
        {"type": "response_item", "timestamp": "2026-07-10T10:00:04Z", "payload": {
            "type": "function_call_output", "call_id": "call-1", "output": "ok",
        }},
        {"type": "event_msg", "timestamp": "2026-07-10T10:00:05Z", "payload": {
            "type": "patch_apply_end", "call_id": "call-2", "success": True,
            "changes": {"a.py": {"type": "update"}},
        }},
        {"type": "event_msg", "timestamp": "2026-07-10T10:00:06Z", "payload": {
            "type": "token_count", "info": {"total_token_usage": {
                "input_tokens": 100,
                "cached_input_tokens": 20,
                "output_tokens": 30,
                "reasoning_output_tokens": 10,
                "total_tokens": 140,
            }},
        }},
        {"type": "event_msg", "timestamp": "2026-07-10T10:00:07Z", "payload": {
            "type": "agent_message", "message": "done", "phase": "final_answer",
        }},
        {"type": "response_item", "timestamp": "2026-07-10T10:00:07Z", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "done"}],
        }},
        {"type": "event_msg", "timestamp": "2026-07-10T10:00:08Z", "payload": {
            "type": "task_complete", "completed_at": "2026-07-10T10:00:08Z",
            "duration_ms": 6000,
        }},
    ])

    conv = list(CodexExtractor().extract(install))[0]

    assert [(message["role"], message["content"]) for message in conv["messages"]] == [
        ("user", "hello"),
        ("assistant", ""),
        ("tool", ""),
        ("assistant", "done"),
    ]
    call = conv["messages"][1]["tool_calls"][0]
    assert call == {
        "id": "call-1",
        "name": "exec_command",
        "input": {},
        "kind": "function",
    }
    result = conv["messages"][2]["tool_result"]
    assert result["tool_call_id"] == "call-1"
    assert result["tool"] == "exec_command"
    assert result["output"] == "ok"
    assert conv["model"] == "gpt-model"
    assert conv["status"] == "completed"
    assert conv["version"] == "0.143.0"
    assert conv["project_path"] == "/work/project"
    assert conv["diff_count"] == 1
    assert conv["token_usage"] == {
        "input": 100,
        "cached_input": 20,
        "output": 30,
        "reasoning": 10,
        "total": 140,
    }


def test_nested_parent_headers_do_not_replace_rollout_identity(tmp_path):
    install = tmp_path / ".codex"
    session = install / "sessions" / "rollout-child.jsonl"
    write_rollout(session, [
        {"type": "session_meta", "payload": {"id": "child", "cwd": "/child"}},
        {"type": "event_msg", "payload": {
            "type": "user_message", "message": "delegated task",
        }},
        {"type": "session_meta", "payload": {"id": "parent", "cwd": "/parent"}},
        {"type": "event_msg", "payload": {
            "type": "agent_message", "message": "done",
        }},
    ])

    conv = list(CodexExtractor().extract(install))[0]

    assert conv["session_id"] == "child"
    assert conv["project_path"] == "/child"


def _zstd_compress(data: bytes) -> bytes:
    try:
        from compression import zstd  # Python 3.14+
    except ImportError:
        import zstandard

        return zstandard.ZstdCompressor().compress(data)
    return zstd.compress(data)


def _rollout_lines(session_id: str, reply: str) -> bytes:
    events = [
        {"type": "session_meta", "ordinal": 0, "payload": {"id": session_id}},
        {"type": "event_msg", "ordinal": 1, "payload": {"type": "user_message", "message": "hello"}},
        {"type": "event_msg", "ordinal": 2, "payload": {"type": "agent_message", "message": reply}},
    ]
    return "".join(json.dumps(ev) + "\n" for ev in events).encode()


def test_compressed_rollouts_are_read(tmp_path):
    install = tmp_path / ".codex"
    day = install / "sessions" / "2026" / "10" / "29"
    day.mkdir(parents=True)
    data = _rollout_lines("zst-1", "hi")
    # Two concatenated frames: Codex may append to a compressed rollout.
    half = data.index(b"\n") + 1
    (day / "rollout-zst-1.jsonl.zst").write_bytes(_zstd_compress(data[:half]) + _zstd_compress(data[half:]))

    convs = list(CodexExtractor().extract(install))

    assert [c["session_id"] for c in convs] == ["zst-1"]
    assert [m["content"] for m in convs[0]["messages"]] == ["hello", "hi"]


def test_session_in_both_forms_is_read_once(tmp_path):
    import os

    install = tmp_path / ".codex"
    day = install / "sessions" / "2026" / "10" / "01"
    day.mkdir(parents=True)
    plain = day / "rollout-dup.jsonl"
    packed = day / "rollout-dup.jsonl.zst"
    plain.write_bytes(_rollout_lines("dup", "old copy"))
    packed.write_bytes(_zstd_compress(_rollout_lines("dup", "new copy")))
    os.utime(plain, (1000, 1000))
    os.utime(packed, (1000, 1000))

    convs = list(CodexExtractor().extract(install))
    assert len(convs) == 1
    assert convs[0]["messages"][1]["content"] == "new copy"

    os.utime(plain, (2000, 2000))  # a newer plain file wins
    convs = list(CodexExtractor().extract(install))
    assert convs[0]["messages"][1]["content"] == "old copy"


def test_extract_empty_install(tmp_path):
    install = tmp_path / ".codex"
    install.mkdir(parents=True)
    assert list(CodexExtractor().extract(install)) == []


def test_find_installations_discovers_codex(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".codex").mkdir()
    installs = CodexExtractor().find_installations()
    assert tmp_path / ".codex" in installs
