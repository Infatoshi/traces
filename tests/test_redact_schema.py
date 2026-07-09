from __future__ import annotations

from traces.redact.rules import RuleContext, make_redactor
from traces.redact.rules.placeholders import PlaceholderBook
from traces.redact.schema import redact_obj


def make_redact():
    ctx = RuleContext(user_name="alexmorgan", home_dir="/Users/alexmorgan")
    return make_redactor(ctx, PlaceholderBook())


def test_redact_obj_passes_through_primitives():
    r = make_redact()
    assert redact_obj(42, r, PlaceholderBook()) == 42
    assert redact_obj(True, r, PlaceholderBook()) is True
    assert redact_obj(None, r, PlaceholderBook()) is None


def test_redact_obj_redacts_strings():
    r = make_redact()
    out = redact_obj("key=sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD", r, PlaceholderBook())
    assert "[SECRET:OPENAI_API_KEY]" in out


def test_redact_obj_recurses_into_list():
    r = make_redact()
    out = redact_obj(["sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD", 7], r, PlaceholderBook())
    assert "[SECRET:OPENAI_API_KEY]" in out[0]
    assert out[1] == 7


def test_redact_obj_recurses_into_dict_keys_and_values():
    r = make_redact()
    out = redact_obj({"normal": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD"}, r, PlaceholderBook())
    assert "[SECRET:OPENAI_API_KEY]" in out["normal"]


def test_sensitive_field_value_replaced_with_marker():
    r = make_redact()
    out = redact_obj({"api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD"}, r, PlaceholderBook())
    assert out["api_key"] == "[REDACTED:SENSITIVE_FIELD]"


def test_share_url_field_replaced_with_share_marker():
    r = make_redact()
    out = redact_obj({"share_url": "https://opncd.ai/share/abc123"}, r, PlaceholderBook())
    assert out["share_url"] == "[REDACTED:SHARE_METADATA]"


def test_encrypted_field_replaced_with_opaque_marker():
    r = make_redact()
    out = redact_obj({"encrypted_content": "abcdef123456"}, r, PlaceholderBook())
    assert out["encrypted_content"] == "[REDACTED:OPAQUE_PROVIDER_BLOB]"


def test_int_value_in_sensitive_field_preserved():
    """Upstream preserves int/float/bool even in sensitive fields."""
    r = make_redact()
    out = redact_obj({"token": 12345}, r, PlaceholderBook())
    assert out["token"] == 12345
