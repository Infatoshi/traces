from __future__ import annotations

import json

from traces.extract.discovery import EXTRACTORS, run_extract


def test_extractors_registry_has_claude_code():
    assert "claude_code" in EXTRACTORS
    assert EXTRACTORS["claude_code"].source == "claude_code"


def test_run_extract_writes_jsonl_one_per_source(tmp_path, monkeypatch):
    # Fake a .claude install with one session
    fake_home = tmp_path / "home"
    fake_claude = fake_home / ".claude"
    proj = fake_claude / "projects" / "p"
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")

    monkeypatch.setenv("HOME", str(fake_home))

    out = tmp_path / "out"
    counts = run_extract(sources=["claude_code"], root=fake_home, out_dir=out)

    assert counts == {"claude_code": 1}
    out_file = out / "raw_extracted" / "claude_code.jsonl"
    assert out_file.exists()
    lines = out_file.read_text().strip().split("\n")
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["source"] == "claude_code"
    assert rec["messages"][0]["content"] == "hi"


def test_run_extract_uses_root_without_home_env(tmp_path, monkeypatch):
    fake_home = tmp_path / "mounted-home"
    fake_codex = fake_home / ".codex"
    session_dir = fake_codex / "sessions" / "2026" / "07" / "06"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-1.jsonl").write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "s1"}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hello"}}) + "\n"
    )

    other_home = tmp_path / "actual-home"
    other_home.mkdir()
    monkeypatch.setenv("HOME", str(other_home))

    out = tmp_path / "out"
    counts = run_extract(sources=["codex"], root=fake_home, out_dir=out)

    assert counts == {"codex": 1}
    rec = json.loads((out / "raw_extracted" / "codex.jsonl").read_text())
    assert rec["source"] == "codex"
    assert rec["messages"][0]["content"] == "hello"


def test_run_extract_searches_linux_layout_from_any_host_os(tmp_path):
    fake_home = tmp_path / "linux-home"
    session_dir = fake_home / ".local" / "share" / "opencode" / "storage" / "message" / "ses_1"
    part_dir = fake_home / ".local" / "share" / "opencode" / "storage" / "part" / "msg_1"
    session_dir.mkdir(parents=True)
    part_dir.mkdir(parents=True)
    (session_dir / "msg_1.json").write_text(json.dumps({
        "id": "msg_1",
        "role": "user",
        "time": {"created": 1},
    }))
    (part_dir / "prt_1.json").write_text(json.dumps({
        "type": "text",
        "text": "from linux layout",
    }))

    out = tmp_path / "out"
    counts = run_extract(sources=["opencode"], root=fake_home, out_dir=out)

    assert counts == {"opencode": 1}
    rec = json.loads((out / "raw_extracted" / "opencode.jsonl").read_text())
    assert rec["source"] == "opencode-cli"
    assert rec["messages"][0]["content"] == "from linux layout"
