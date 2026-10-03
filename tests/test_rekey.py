import hashlib
import json

from traces.rekey import LEGACY_RE, rekey_digest, rekey_tree

KEY = "test-key"


def _legacy(cat: str, n: int, value: str) -> str:
    return f"[{cat}:{n:04d}:{hashlib.sha256(value.encode()).hexdigest()[:10]}]"


def test_rekey_tree_rewrites_legacy_only(tmp_path):
    email = _legacy("EMAIL", 1, "someone@example.com")
    home = _legacy("HOME_PATH", 12, "/home/someone")
    rows = [
        {"messages": [{"content": f"mail {email} at {home}, again {email}"}],
         "metadata": {"trace_id": "ts_abc"}},
        {"messages": [{"content": "keyed [EMAIL:0123456789] and [SECRET:KNOWN_VALUE] stay"}],
         "metadata": {"trace_id": "ts_def"}},
    ]
    path = tmp_path / "messages.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    before = path.read_bytes()

    check = rekey_tree(tmp_path, KEY, fix=False, jobs=2)
    assert check["occurrences"] == 3 and path.read_bytes() == before

    report = rekey_tree(tmp_path, KEY, fix=True, jobs=2)
    assert report["by_category"] == {"EMAIL": 2, "HOME_PATH": 1}
    out = [json.loads(line) for line in path.read_text().splitlines()]
    text = out[0]["messages"][0]["content"]
    digest = hashlib.sha256(b"someone@example.com").hexdigest()[:10]
    assert digest not in text and not LEGACY_RE.search(path.read_bytes())
    new_email = f"[EMAIL:{rekey_digest(KEY.encode(), digest.encode())}]"
    assert text.count(new_email) == 2
    assert out[1] == rows[1] and out[0]["metadata"] == rows[0]["metadata"]

    again = rekey_tree(tmp_path, KEY, fix=True, jobs=1)
    assert again["occurrences"] == 0


def test_rekey_needs_key(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        rekey_tree(tmp_path, "", fix=False)
