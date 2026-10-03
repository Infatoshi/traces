"""Known-value scrub, harvest, and export merge."""
from __future__ import annotations

import json

from traces.harvest import Harvest, plausible
from traces.merge import merge_exports
from traces.scrub import PLACEHOLDER, scrub_tree

SECRET = "sekretVALUE1234567890"
QUOTED = 'ab"cd\\ef0123456789xyz'


def test_scrub_replaces_plain_and_escaped_values(tmp_path):
    rows = [
        {"messages": [{"content": f"key {SECRET} and {QUOTED} end"}], "metadata": {"trace_id": "t1"}},
        {"messages": [{"content": "clean"}], "metadata": {"trace_id": "t2"}},
    ]
    path = tmp_path / "messages.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    values = [("h:.env_vars:API_KEY", SECRET), ("h:.env:OTHER", QUOTED)]
    check = scrub_tree(tmp_path, values, fix=False, jobs=1)
    assert check["occurrences"] == 2
    assert SECRET not in json.dumps(check)
    fixed = scrub_tree(tmp_path, values, fix=True, jobs=2)
    assert fixed["occurrences"] == 2
    text = path.read_text()
    assert SECRET not in text and "ef0123456789" not in text
    first = json.loads(text.splitlines()[0])
    assert first["messages"][0]["content"] == f"key {PLACEHOLDER} and {PLACEHOLDER} end"
    assert scrub_tree(tmp_path, values, fix=False, jobs=1)["occurrences"] == 0


def test_harvest_reads_env_files_and_filters(tmp_path):
    env = tmp_path / ".env_vars"
    env.write_text(
        "export OPENAI_API_KEY=sk-proj-abc123def456ghi789\n"
        "export X_API_KEY='GWIGtSuPmsUsysHEEywRoVQx'\n"
        "export MODEL=claude-sonnet\n"
        "export HOME_DIR=/home/someone/dev/project\n"
    )
    h = Harvest("host")
    h.env_file(env, secret_named_only=False)
    assert set(h.values) == {"sk-proj-abc123def456ghi789", "GWIGtSuPmsUsysHEEywRoVQx"}
    assert not plausible("short1")


def _export(d, rows):
    d.mkdir(parents=True)
    with (d / "messages.jsonl").open("w") as f:
        for tid, n in rows:
            f.write(json.dumps({"messages": [{"role": "user", "content": "x"}] * n,
                                "metadata": {"trace_id": tid, "source": "s", "counts": {"messages": n}}}) + "\n")
    with (d / "execution.jsonl").open("w") as f:
        for tid, _n in rows:
            f.write(json.dumps({"schema_version": "execution.v1", "execution_id": f"{tid}:task:0",
                                "messages": [], "metadata": {"trace_id": tid}}) + "\n")


def test_merge_keeps_longest_and_old_only(tmp_path):
    _export(tmp_path / "new", [("A", 5), ("B", 3)])
    _export(tmp_path / "mid", [("X", 7), ("Z", 4)])
    (tmp_path / "mid" / "idmap.json").write_text(json.dumps({"X": "A"}))
    _export(tmp_path / "old", [("A", 4), ("C", 6)])
    report = merge_exports(tmp_path / "out", [tmp_path / "new", tmp_path / "mid", tmp_path / "old"])
    ids = [json.loads(line)["metadata"]["trace_id"] for line in (tmp_path / "out" / "messages.jsonl").open()]
    assert sorted(ids) == ["A", "B", "C", "Z"]
    execution = (tmp_path / "out" / "execution.jsonl").read_text()
    assert '"A:task:0"' in execution and '"X:task:0"' not in execution
    assert report["remapped_ids"] == 1 and report["older_version_kept_because_longer"] == 1


def test_drop_traces_removes_all_variants(tmp_path):
    from traces.merge import drop_traces

    _export(tmp_path / "t", [("A", 2), ("B", 3)])
    removed = drop_traces(tmp_path / "t", {"A"})
    assert removed == {"execution.jsonl": 1, "messages.jsonl": 1}
    text = (tmp_path / "t" / "messages.jsonl").read_text() + (tmp_path / "t" / "execution.jsonl").read_text()
    assert '"A"' not in text and '"B"' in text


def test_token_count_survives_bad_strings():
    import tiktoken

    from traces import tokens

    tokens._ENC = tiktoken.get_encoding(tokens.ENCODING)
    n, approx = tokens.count_text("plain text with a lone \ud800 surrogate")
    assert n > 0 and approx == 0
    assert tokens.count_text("hello world")[0] == 2
