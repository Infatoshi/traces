"""The Hyperscan prefilter and process pools must not change any result."""
from __future__ import annotations

import json
import re

import pytest

from traces import fastmatch
from traces.config import RedactorConfig
from traces.redact.pipeline import run_redact
from traces.redact.rules import RuleContext, build_patterns, redact_string
from traces.redact.rules.placeholders import PlaceholderBook
from traces.verify import scanner

pytestmark = pytest.mark.skipif(fastmatch.hyperscan is None, reason="hyperscan not installed")

SAMPLES = [
    "key=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD and ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "Authorization: Bearer abcdefghijklmnop1234567890 from alexmorgan@laptop.local",
    'X_ACCESS_TOKEN_SECRET = \\"cBeNfakeFAKE1234567890abcdefghij\\" then API_KEY: ${FOO:-abc123}',
    "cat /Users/alexmorgan/dev/project/.env && ssh alexmorgan@10.0.0.12 -p 22",
    "Alex Morgan lives at 1234 Main Street; call me: +1 (555) 123-4567, alex@example.com",
    "fe80::1ff:fe23:4567:890a at 51.50722, -0.12750 on 12:34:56 and aa:bb:cc:dd:ee:ff",
    "postgres://user:hunter2@db.internal:5432/app and https://example-private.com/share/x",
    "AKIAABCDEFGHIJKLMNOP eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    "plain prose with no secrets, café, 東京, emoji 🙂, and a long run " + "A" * 5000,
    "input_tokens: 123456789012 tokenizer: meta-llama/Llama-3.1-8B",
    "",
]


def _ctx() -> RuleContext:
    return RuleContext(
        user_name="alexmorgan",
        home_dir="/Users/alexmorgan",
        private_terms=["alexmorgan", "Alex Morgan", "buildbox"],
        private_domains=["example-private.com"],
    )


def test_prefilter_redaction_is_identical():
    patterns = build_patterns(_ctx())
    prefilter = fastmatch.Prefilter([p for _n, p, _r in patterns])
    assert prefilter.active
    for text in SAMPLES:
        slow_book, fast_book = PlaceholderBook(), PlaceholderBook()
        slow = redact_string(text, patterns, slow_book)
        fast = redact_string(text, patterns, fast_book, prefilter)
        assert fast == slow, text
        assert fast_book.counts == slow_book.counts, text


def test_prefilter_is_superset_of_python_matches():
    patterns = build_patterns(_ctx())
    prefilter = fastmatch.Prefilter([p for _n, p, _r in patterns])
    for text in SAMPLES:
        candidates = prefilter.candidates(text)
        for index, (_name, pattern, _rep) in enumerate(patterns):
            if pattern.search(text):
                assert index in candidates, (_name, text)


def test_unencodable_text_falls_back_to_all_patterns():
    prefilter = fastmatch.Prefilter([re.compile("x")])
    assert prefilter.candidates("lone \ud800 surrogate") is None


def _write_rows(path, n=300):
    rows = []
    for i in range(n):
        text = SAMPLES[i % len(SAMPLES)] if i % 7 == 0 else f"clean row {i}"
        rows.append(json.dumps({"messages": [{"role": "user", "content": text}]}))
    path.write_text("\n".join(rows) + "\n")


def test_parallel_scan_and_clean_match_serial(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "line_ranges", lambda p: _small_ranges(p))
    src = tmp_path / "in"
    src.mkdir()
    _write_rows(src / "a.jsonl")
    serial = scanner.scan(src, jobs=1)
    parallel = scanner.scan(src, jobs=3)
    assert [(f.line_no, f.category) for f in parallel.leftovers] == [
        (f.line_no, f.category) for f in serial.leftovers
    ]
    assert serial.leftovers
    one = scanner.clean_tree(src, tmp_path / "one", jobs=1)
    many = scanner.clean_tree(src, tmp_path / "many", jobs=3)
    assert one == many
    assert (tmp_path / "one" / "a.jsonl").read_bytes() == (tmp_path / "many" / "a.jsonl").read_bytes()


def test_parallel_redact_matches_serial(tmp_path, monkeypatch):
    from traces.redact import pipeline

    monkeypatch.setattr(pipeline, "line_ranges", lambda p: _small_ranges(p))
    raw = tmp_path / "raw"
    raw.mkdir()
    _write_rows(raw / "claude_code.jsonl")
    config = RedactorConfig(user_name="alexmorgan", home_dir="/Users/alexmorgan",
                            private_terms=["Alex Morgan"])
    serial = run_redact(raw, tmp_path / "s", config, jobs=1)
    parallel = run_redact(raw, tmp_path / "p", config, jobs=3)
    assert dict(serial["counts"]) == dict(parallel["counts"])
    s = (tmp_path / "s" / "claude_code.jsonl").read_bytes()
    assert s == (tmp_path / "p" / "claude_code.jsonl").read_bytes()


def _small_ranges(path):
    from traces.parallel import line_ranges

    return line_ranges(path, target=2000)
