"""Tests for the split rule modules."""
from __future__ import annotations

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
    # home_path replacement is None -> uses stable() -> [HOME_PATH:0001:sha10]
    assert "[HOME_PATH:0001:" in out
    # Same value redacted twice produces same placeholder
    out2 = redact_string("ls /customroot/secret/file", patterns, book)
    assert out2.count("[HOME_PATH:0001:") == 1


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
