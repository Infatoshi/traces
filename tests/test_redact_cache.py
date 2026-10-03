"""The redacted-block cache must reproduce uncached output exactly."""
from __future__ import annotations

import json

from traces.config import RedactorConfig
from traces.redact.pipeline import run_redact

KEY = "0" * 64


def _record(i: int, extra: int = 0) -> dict:
    msgs = [
        {"role": "user", "content": f"cat /Users/alexmorgan/p{i}/.env and mail alex{i}@example.com"},
        {"role": "assistant", "content": f"OPENAI_API_KEY=sk-proj-{'a' * 30}{i} found"},
    ]
    msgs += [{"role": "user", "content": f"follow-up {j} from Alex Morgan"} for j in range(extra)]
    return {"session_id": f"s{i}", "messages": msgs, "title": "Alex Morgan project"}


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _config(tmp_path, **kw):
    return RedactorConfig(user_name="alexmorgan", home_dir="/Users/alexmorgan",
                          private_terms=["Alex Morgan"], placeholder_key=KEY, **kw)


def test_cache_hits_reproduce_uncached_output(tmp_path):
    raw = tmp_path / "raw"
    _write(raw / "claude_code.jsonl", [_record(i) for i in range(20)])
    plain = run_redact(raw, tmp_path / "plain", _config(tmp_path))
    cfg = _config(tmp_path, cache_dir=str(tmp_path / "cache"))
    first = run_redact(raw, tmp_path / "first", cfg)
    second = run_redact(raw, tmp_path / "second", cfg)
    expected = (tmp_path / "plain" / "claude_code.jsonl").read_bytes()
    assert (tmp_path / "first" / "claude_code.jsonl").read_bytes() == expected
    assert (tmp_path / "second" / "claude_code.jsonl").read_bytes() == expected
    assert first["cache"]["misses"] == 40 and first["cache"]["hits"] == 0
    assert second["cache"]["hits"] == 40 and second["cache"]["misses"] == 0
    assert dict(plain["counts"]) == dict(first["counts"]) == dict(second["counts"])


def test_grown_session_only_redacts_new_messages(tmp_path):
    raw = tmp_path / "raw"
    cfg = _config(tmp_path, cache_dir=str(tmp_path / "cache"))
    _write(raw / "codex.jsonl", [_record(1)])
    run_redact(raw, tmp_path / "a", cfg)
    _write(raw / "codex.jsonl", [_record(1, extra=3)])
    report = run_redact(raw, tmp_path / "b", cfg)
    assert report["cache"]["hits"] == 2 and report["cache"]["misses"] == 3


def test_rule_context_change_uses_fresh_cache(tmp_path):
    raw = tmp_path / "raw"
    _write(raw / "codex.jsonl", [_record(1)])
    a = run_redact(raw, tmp_path / "a", _config(tmp_path, cache_dir=str(tmp_path / "cache")))
    changed = RedactorConfig(user_name="alexmorgan", home_dir="/Users/alexmorgan",
                             private_terms=["Alex Morgan", "project"], placeholder_key=KEY,
                             cache_dir=str(tmp_path / "cache"))
    b = run_redact(raw, tmp_path / "b", changed)
    assert a["cache"]["file"] != b["cache"]["file"]
    assert b["cache"]["hits"] == 0


def test_parallel_cached_matches_serial_uncached(tmp_path, monkeypatch):
    from traces.parallel import line_ranges
    from traces.redact import pipeline

    monkeypatch.setattr(pipeline, "line_ranges", lambda p: line_ranges(p, target=500))
    raw = tmp_path / "raw"
    _write(raw / "grok_build.jsonl", [_record(i, extra=i % 3) for i in range(30)])
    run_redact(raw, tmp_path / "plain", _config(tmp_path))
    cfg = _config(tmp_path, cache_dir=str(tmp_path / "cache"))
    run_redact(raw, tmp_path / "p1", cfg, jobs=3)
    report = run_redact(raw, tmp_path / "p2", cfg, jobs=3)
    expected = (tmp_path / "plain" / "grok_build.jsonl").read_bytes()
    assert (tmp_path / "p1" / "grok_build.jsonl").read_bytes() == expected
    assert (tmp_path / "p2" / "grok_build.jsonl").read_bytes() == expected
    assert report["cache"]["misses"] == 0
