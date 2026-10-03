"""Tests for the split rule modules."""
from __future__ import annotations

import re

from traces.redact.rules import RuleContext, build_patterns, redact_string
from traces.redact.rules.placeholders import PlaceholderBook


def make_book_and_patterns(user_name="alexmorgan", home_dir="/Users/alexmorgan", **ctx_kwargs):
    ctx = RuleContext(user_name=user_name, home_dir=home_dir, **ctx_kwargs)
    book = PlaceholderBook()
    patterns = build_patterns(ctx)
    return book, patterns


def test_openai_api_key_redacted():
    book, patterns = make_book_and_patterns()
    out = redact_string("key=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD", patterns, book)
    assert "[SECRET:OPENAI_API_KEY]" in out
    assert "sk-proj-" not in out


def test_github_token_redacted():
    book, patterns = make_book_and_patterns()
    out = redact_string("ghp_abcdefghijklmnopqrstuvwxyz0123456789", patterns, book)
    assert "[SECRET:GITHUB_TOKEN]" in out


def test_bearer_token_redacted():
    book, patterns = make_book_and_patterns()
    out = redact_string("Authorization: Bearer abcdefghijklmnop1234567890", patterns, book)
    assert "[SECRET:BEARER_TOKEN]" in out


def test_home_path_stable_placeholder():
    # Use a home_dir that does NOT contain the username, so private_user_name
    # doesn't shadow home_path. (UPSTREAM_ORDER runs private_user_name before
    # home_path; if the username appeared in the path, it would be consumed first.)
    book, patterns = make_book_and_patterns(user_name="tester", home_dir="/customroot")
    out = redact_string("cat /customroot/secret/file", patterns, book)
    # home_path replacement is None -> uses stable() -> [HOME_PATH:hmac10]
    placeholder = re.search(r"\[HOME_PATH:[0-9a-f]{10}\]", out)
    assert placeholder
    # Same value redacted twice produces same placeholder, even from a new book
    out2 = redact_string("ls /customroot/secret/file", patterns, PlaceholderBook(book.key))
    assert out2.count(placeholder.group(0)) == 1
    # A different key gives a different placeholder
    out3 = redact_string("ls /customroot/secret/file", patterns, PlaceholderBook(b"other"))
    assert placeholder.group(0) not in out3


def test_email_redacted_stable():
    book, patterns = make_book_and_patterns()
    out = redact_string("contact alex.morgan@example.com", patterns, book)
    assert "[EMAIL:" in out
    assert "alex.morgan@" not in out


def test_private_url_redacted_public_url_default_off():
    book, patterns = make_book_and_patterns()  # allow_public_urls defaults False
    out = redact_string("see https://docs.python.org/3/library", patterns, book)
    # All URLs redacted by default
    assert "docs.python.org" not in out


def test_public_url_allowlist_preserves_python_docs():
    book, patterns = make_book_and_patterns(allow_public_urls=True)
    out = redact_string("see https://docs.python.org/3/library", patterns, book)
    assert "docs.python.org" in out


def test_private_term_redacted():
    book, patterns = make_book_and_patterns(private_terms=["AcmeClient"])
    out = redact_string("Working on AcmeClient integration", patterns, book)
    assert "AcmeClient" not in out
    assert "[PRIVATE_TERM" in out


def test_phone_redacted():
    book, patterns = make_book_and_patterns()
    out = redact_string("phone: +1-555-867-5309", patterns, book)
    assert "[PII:PHONE]" in out


def test_jwt_redacted():
    book, patterns = make_book_and_patterns()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    out = redact_string(jwt, patterns, book)
    assert "[SECRET:JWT]" in out


def test_env_assignment_with_escaped_quotes_redacted():
    book, patterns = make_book_and_patterns()
    out = redact_string(
        'X_ACCESS_TOKEN_SECRET = \\"cBeNfakeFAKE1234567890abcdefghij\\"', patterns, book
    )
    assert "cBeNfake" not in out
    assert "[SECRET:ENV_VALUE]" in out


def test_colon_style_secrets_redacted():
    book, patterns = make_book_and_patterns()
    for text, leaked in [
        ("OPENAI_API_KEY: SETfake123def456ghi789jkl", "fake123"),
        ("x-api-key: fakeKEY1234567890abcdefgh", "fakeKEY"),
        ("AWS Secret Access Key: ZzMaFAKEfake1234567890abcdefgh", "ZzMa"),
        ('Primary auth token": 6bcffake0123456789abcdef01234567', "6bcf"),
    ]:
        out = redact_string(text, patterns, book)
        assert leaked not in out, text


def test_colon_style_non_secrets_kept():
    book, patterns = make_book_and_patterns()
    for text in [
        "input_tokens: 123456789012",
        "tokenizer: meta-llama/Llama-3.1-8B",
        '"max_tokens": 4096',
        "Token: see the docs",
    ]:
        assert redact_string(text, patterns, book) == text
