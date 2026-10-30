"""Tests for the Grok Build extractor."""
from __future__ import annotations

import json
from pathlib import Path

from traces.extract.grok_build import GrokBuildExtractor, extract_grok_session


def _write_session(
    root: Path,
    cwd_encoded: str,
    session_id: str,
    lines: list[dict],
    summary: dict | None = None,
) -> Path:
    sess = root / "sessions" / cwd_encoded / session_id
    sess.mkdir(parents=True)
    with (sess / "chat_history.jsonl").open("w") as f:
        for obj in lines:
            f.write(json.dumps(obj) + "\n")
    if summary is not None:
        (sess / "summary.json").write_text(json.dumps(summary))
    return sess


def test_extract_basic_user_assistant(tmp_path):
    home = tmp_path / ".grok"
    _write_session(
        home,
        "%2Ftmp%2Fproj",
        "sess-1",
        [
            {"type": "system", "content": "You are Grok."},
            {"type": "user", "content": [{"type": "text", "text": "hello"}]},
            {
                "type": "assistant",
                "content": "hi there",
                "model_id": "grok-4.5",
            },
        ],
        summary={
            "info": {"id": "sess-1", "cwd": "/tmp/proj"},
            "generated_title": "Hello",
            "current_model_id": "grok-4.5",
            "created_at": "2026-07-10T01:02:03Z",
            "updated_at": "2026-07-11T04:05:06Z",
            "chat_format_version": 1,
            "reasoning_effort": "high",
            "session_kind": "subagent",
            "sandbox_profile": "off",
        },
    )

    convs = list(GrokBuildExtractor(tmp_path).extract(home))
    assert len(convs) == 1
    conv = convs[0]
    assert conv["source"] == "grok_build"
    assert conv["session_id"] == "sess-1"
    assert conv["project_path"] == "/tmp/proj"
    assert conv["name"] == "Hello"
    assert conv["model"] == "grok-4.5"
    assert conv["created_at"] == "2026-07-10T01:02:03Z"
    assert conv["updated_at"] == "2026-07-11T04:05:06Z"
    assert conv["version"] == 1
    assert conv["reasoning_effort"] == "high"
    assert conv["session_kind"] == "subagent"
    assert conv["sandbox_profile"] == "off"
    roles = [m["role"] for m in conv["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert conv["messages"][1]["content"] == "hello"
    assert conv["messages"][2]["content"] == "hi there"
    assert conv["messages"][2]["model"] == "grok-4.5"


def test_extract_tool_calls_and_results(tmp_path):
    home = tmp_path / ".grok"
    _write_session(
        home,
        "cwd",
        "s2",
        [
            {"type": "user", "content": "run ls"},
            {
                "type": "assistant",
                "content": "ok",
                "model_id": "grok-4.5",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "run_terminal_command",
                        "arguments": json.dumps({"command": "ls"}),
                    }
                ],
            },
            {
                "type": "tool_result",
                "tool_call_id": "call-1",
                "content": "file.txt",
            },
        ],
    )

    conv = extract_grok_session(home / "sessions" / "cwd" / "s2")
    assert conv is not None
    assert conv["messages"][1]["role"] == "assistant"
    assert conv["messages"][1]["tool_uses"][0]["name"] == "run_terminal_command"
    assert conv["messages"][1]["tool_uses"][0]["input"] == {"command": "ls"}
    assert conv["messages"][2]["role"] == "tool"
    assert conv["messages"][2]["content"] == "file.txt"
    assert conv["messages"][2]["tool_result"] == {
        "tool_call_id": "call-1",
        "output": "file.txt",
    }


def test_extract_reasoning_summary(tmp_path):
    home = tmp_path / ".grok"
    _write_session(
        home,
        "cwd",
        "s3",
        [
            {"type": "user", "content": "think"},
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "planning steps"}],
                "encrypted_content": "opaque",
                "model_id": "grok-4.5",
            },
            {"type": "assistant", "content": "done", "model_id": "grok-4.5"},
        ],
    )
    conv = extract_grok_session(home / "sessions" / "cwd" / "s3")
    assert conv is not None
    assert conv["messages"][1]["role"] == "assistant"
    assert "planning steps" in conv["messages"][1]["content"]
    assert "<reasoning>" in conv["messages"][1]["content"]
    assert conv["messages"][1]["phase"] == "reasoning"


def test_extract_empty_install(tmp_path):
    home = tmp_path / ".grok"
    home.mkdir()
    (home / "sessions").mkdir()
    assert list(GrokBuildExtractor().extract(home)) == []


def test_find_installations_discovers_grok(tmp_path):
    (tmp_path / ".grok" / "sessions").mkdir(parents=True)
    installs = GrokBuildExtractor(tmp_path).find_installations()
    assert any(p.name == ".grok" for p in installs)


def test_cwd_from_encoded_parent_when_summary_missing(tmp_path):
    home = tmp_path / ".grok"
    _write_session(
        home,
        "%2FUsers%2Finfatoshi%2Fdev",
        "s4",
        [{"type": "user", "content": "x"}, {"type": "assistant", "content": "y"}],
    )
    conv = extract_grok_session(
        home / "sessions" / "%2FUsers%2Finfatoshi%2Fdev" / "s4"
    )
    assert conv is not None
    assert conv["project_path"] == "/Users/infatoshi/dev"


def _update(kind: str, **fields) -> dict:
    return {"method": "session/update", "params": {"sessionId": "s", "update": {"sessionUpdate": kind, **fields}}}


def _prompt(index: int, text: str, hidden: bool = False) -> dict:
    meta = {"modelId": "grok-4.6", "promptIndex": index}
    if hidden:
        meta["hideFromScrollback"] = True
    return _update("user_message_chunk", content={"type": "text", "text": text}, _meta=meta)


def _call(call_id: str, name: str, args: dict) -> dict:
    return _update("tool_call", toolCallId=call_id, title=name, rawInput=args)


def _done(call_id: str, raw: dict | None = None, content: list | None = None, status: str = "completed") -> dict:
    return _update("tool_call_update", toolCallId=call_id, status=status, rawOutput=raw, content=content)


def _turn_done() -> dict:
    return _update("turn_completed", usage={"modelUsage": {"grok-4.6-build": {"totalTokens": 10}}})


def _write_updates(sess: Path, events: list[dict]) -> None:
    with (sess / "updates.jsonl").open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_compacted_session_rebuilt_from_updates(tmp_path):
    """chat_history after a compaction keeps only the tail; updates.jsonl has every turn."""
    home = tmp_path / ".grok"
    summary_text = "This session is being continued from a previous conversation. Summary: listed files."
    sess = _write_session(
        home,
        "cwd",
        "s5",
        [
            {"type": "system", "content": "You are Grok."},
            {"type": "user", "synthetic_reason": "compaction_meta", "content": [{"type": "text", "text": "<user_info>\nOS: macos\n</user_info>"}]},
            {"type": "user", "synthetic_reason": "compaction_meta", "content": [{"type": "text", "text": summary_text}]},
            {"type": "user", "prompt_index": 1, "content": [{"type": "text", "text": "<user_query>\nnow grep\n</user_query>"}]},
            {"type": "assistant", "content": "", "tool_calls": [{"id": "c3", "name": "grep", "arguments": "{}"}]},
            {"type": "tool_result", "tool_call_id": "c3", "content": "a.py:1:x"},
        ],
    )
    (sess / "compaction_checkpoints").mkdir()
    (sess / "compaction_checkpoints" / "k1.json").write_text(json.dumps({
        "compacted_history": [
            {"type": "user", "synthetic_reason": "compaction_meta", "content": [{"type": "text", "text": summary_text}]},
        ],
    }))
    _write_updates(sess, [
        _prompt(0, "list files"),
        _update("agent_thought_chunk", content={"type": "text", "text": "use ls"}),
        _update("agent_message_chunk", content={"type": "text", "text": "Listing."}),
        _call("c1", "run_terminal_command", {"command": "ls"}),
        _call("c2", "read_file", {"file_path": "/tmp/shot.png"}),
        _done("c1", raw={"type": "Bash", "output": list(b"a.py\n"), "output_for_prompt": "exit: 0\na.py\n"}),
        _done("c2", raw={"type": "ReadFile", "ImageContent": {"data": "iVBOR" * 100}}),
        _turn_done(),
        _update("compaction_checkpoint", checkpoint_file="compaction_checkpoints/k1.json"),
        _prompt(1, "now grep"),
        _call("c3", "grep", {}),
        _done("c3", raw={"type": "GrepSearch", "stdout": list(b"a.py:1:x")}),
        _prompt(2, '<system-reminder>\nBackground task "t" completed.\n</system-reminder>', hidden=True),
        _update("agent_message_chunk", content={"type": "text", "text": "Done."}),
        _turn_done(),
    ])

    conv = extract_grok_session(sess)
    assert conv is not None
    assert conv["source_file"].endswith("updates.jsonl")
    msgs = conv["messages"]
    assert [m["role"] for m in msgs] == [
        "system", "user", "user", "assistant", "assistant", "tool", "tool",
        "user", "user", "assistant", "tool", "user", "assistant",
    ]
    assert msgs[1]["content"].startswith("<user_info>")
    assert msgs[2]["content"] == "<user_query>\nlist files\n</user_query>"
    assert msgs[3] == {"role": "assistant", "content": "<reasoning>\nuse ls\n</reasoning>", "phase": "reasoning", "model": "grok-4.6-build"}
    assert msgs[4]["content"] == "Listing."
    assert [u["name"] for u in msgs[4]["tool_uses"]] == ["run_terminal_command", "read_file"]
    assert msgs[4]["tool_uses"][0]["input"] == {"command": "ls"}
    assert msgs[5]["content"] == "exit: 0\na.py\n"
    assert msgs[5]["tool_result"] == {"tool_call_id": "c1", "output": "exit: 0\na.py\n", "status": "completed"}
    assert msgs[6]["content"] == "Read image file: /tmp/shot.png"
    assert msgs[7]["content"] == summary_text
    assert msgs[8]["content"] == "<user_query>\nnow grep\n</user_query>"
    assert msgs[10]["content"] == "a.py:1:x"
    assert msgs[11]["content"].startswith("<system-reminder>")
    assert msgs[12]["content"] == "Done."


def test_complete_chat_history_is_kept_over_updates(tmp_path):
    home = tmp_path / ".grok"
    sess = _write_session(
        home,
        "cwd",
        "s6",
        [
            {"type": "user", "prompt_index": 0, "content": [{"type": "text", "text": "<user_query>\nls\n</user_query>"}]},
            {"type": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "run_terminal_command", "arguments": "{}"}]},
            {"type": "tool_result", "tool_call_id": "c1", "content": "exit: 0\na.py\n"},
        ],
    )
    _write_updates(sess, [
        _prompt(0, "ls"),
        _call("c1", "run_terminal_command", {}),
        _done("c1", raw={"type": "Bash", "output_for_prompt": "exit: 0\na.py\n"}),
    ])
    conv = extract_grok_session(sess)
    assert conv is not None
    assert conv["source_file"].endswith("chat_history.jsonl")
    assert conv["messages"][2]["tool_result"] == {"tool_call_id": "c1", "output": "exit: 0\na.py\n"}


def test_updates_rewind_drops_discarded_turns(tmp_path):
    home = tmp_path / ".grok"
    sess = _write_session(home, "cwd", "s7", [{"type": "user", "content": "<user_query>\nfirst\n</user_query>"}])
    _write_updates(sess, [
        _prompt(0, "first"),
        _call("c1", "list_dir", {}),
        _done("c1", raw={"type": "ListDir", "Content": {"content": "a\nb"}}),
        _prompt(1, "wrong turn"),
        _call("c2", "list_dir", {}),
        _done("c2", status="failed", content=[{"type": "content", "content": {"type": "text", "text": "boom"}}]),
        _update("rewind_marker", target_prompt_index=1),
        _prompt(1, "right turn"),
        _call("c3", "todo_write", {}),
        _done("c3", raw={"type": "Todo", "TodosUpdated": {"summary_for_prompt": "- [pending] x"}}),
    ])
    conv = extract_grok_session(sess)
    assert conv is not None
    texts = [m["content"] for m in conv["messages"]]
    assert "<user_query>\nwrong turn\n</user_query>" not in texts
    assert texts == [
        "<user_query>\nfirst\n</user_query>", "", "a\nb",
        "<user_query>\nright turn\n</user_query>", "", "- [pending] x",
    ]


def test_tool_output_falls_back_to_json_without_inline_images():
    from traces.extract.grok_build import _tool_output_text

    failed = {"status": "failed", "rawOutput": None,
              "content": [{"type": "content", "content": {"type": "text", "text": "No such file"}}]}
    assert _tool_output_text(failed) == "No such file"
    raw = {"type": "Monitor", "taskId": "m1", "frame": {"data": "A" * 1000}}
    assert json.loads(_tool_output_text({"rawOutput": raw})) == {"type": "Monitor", "taskId": "m1", "frame": "[image]"}
    diff = {"content": [{"type": "diff", "path": "f.c", "oldText": "a", "newText": "b"}]}
    assert _tool_output_text(diff) == "f.c\n--- old\na\n+++ new\nb"
