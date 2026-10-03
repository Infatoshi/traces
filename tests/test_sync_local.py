"""End-to-end work-side sync on a synthetic home (no network, no publish)."""
from __future__ import annotations

import json

from traces.sync import sync_local

SECRET = "zzLiteralSecretValue0123456789"


def _home(tmp_path):
    home = tmp_path / "home"
    session = home / ".claude" / "projects" / "-p" / "sess-1.jsonl"
    session.parent.mkdir(parents=True)
    events = [
        {"type": "user", "sessionId": "sess-1", "timestamp": "2026-09-01T00:00:00Z",
         "message": {"content": f"deploy with token {SECRET} from /home/alexmorgan/app"}},
        {"type": "assistant", "sessionId": "sess-1", "timestamp": "2026-09-01T00:00:05Z",
         "message": {"model": "m", "content": [{"type": "text", "text": "done, Alex Morgan"}]}},
    ]
    session.write_text("".join(json.dumps(e) + "\n" for e in events))
    return home


def test_sync_local_end_to_end_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACES_METADATA_KEY", "k" * 64)
    home = _home(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    (work / "stores.json").write_text(json.dumps([{"label": "h1", "root": str(home), "home": "/home/alexmorgan"}]))
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "h1.tsv").write_text(f"h1:.env_vars:DEPLOY_TOKEN\t{SECRET}\n")
    cfg = {
        "identity": {"user": "alexmorgan", "private_terms": ["Alex Morgan"]},
        "run": {"jobs": 2, "cache_dir": str(tmp_path / "cache")},
        "sync": {"sources": ["claude_code"]},
    }
    first = sync_local(cfg, work, secrets, publish=False)
    final = work / "run" / "final"
    text = "".join(p.read_text() for p in final.glob("*.jsonl"))
    assert SECRET not in text and "alexmorgan" not in text and "Alex Morgan" not in text
    assert first["verify_ok"] and first["export_summary"]["messages_rows"] == 1
    assert (final / "MANIFEST.json").exists()
    assert first["hosts"]["h1"]["claude_code"] == {"extracted": 1, "published": 1}
    tokens = first["export_summary"]["tokens"]
    assert tokens["tokens"] > tokens["assistant_tokens"] > 0
    assert tokens["by_model"]["m"]["traces"] == 1
    first_bytes = (final / "messages.jsonl").read_bytes()

    second = sync_local(cfg, work, secrets, publish=False)
    cache = second["redact_cache"]["h1"]
    assert cache["hits"] > 0 and cache["misses"] == 0
    assert (work / "run" / "final" / "messages.jsonl").read_bytes() == first_bytes


def test_published_rows_reredacted_only_when_rules_change(tmp_path, monkeypatch):
    import shutil

    from traces import sync as sync_mod

    monkeypatch.setenv("TRACES_METADATA_KEY", "k" * 64)
    home = _home(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    (work / "stores.json").write_text(json.dumps([{"label": "h1", "root": str(home), "home": "/home/alexmorgan"}]))
    cfg = {
        "identity": {"user": "alexmorgan", "private_terms": ["Alex Morgan"]},
        "run": {"jobs": 1, "cache_dir": str(tmp_path / "cache")},
        "sync": {"sources": ["claude_code"]},
    }
    sync_local(cfg, work, None, publish=False)
    published = tmp_path / "published"
    shutil.copytree(work / "run" / "final", published)

    def fake_pull(repo, out, revision=None):
        shutil.copytree(published, out)
        return {"repo": repo, "revision": "x", "files": []}

    monkeypatch.setattr("traces.merge.pull_published", fake_pull)
    cfg["publish"] = {"repo": "someone/traces"}
    same = sync_mod.sync_local(cfg, work, None, publish=False)
    assert same["published"]["reredacted"] is False
    assert same["export_summary"]["messages_rows"] == 1

    cfg["identity"]["private_terms"] = ["Alex Morgan", "deploy"]
    changed = sync_mod.sync_local(cfg, work, None, publish=False)
    assert changed["published"]["reredacted"] is not False
    assert changed["export_summary"]["messages_rows"] == 1
