from __future__ import annotations

from autopilot.redaction import contains_secret, redact, redact_value


def test_redacts_common_token_shapes() -> None:
    text = "token ghp_abcdefghijklmnopqrstuvwxyz0123 and key sk-abcdefghijklmnopqrstuvwx"
    result = redact(text)
    assert result.changed
    assert "ghp_" not in result.text
    assert "sk-" not in result.text
    assert "github_token" in result.hits
    assert "openai_key" in result.hits


def test_redacts_private_key_blocks() -> None:
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    result = redact(f"here it is {block} done")
    assert "MIIEow" not in result.text
    assert "private_key" in result.hits
    assert result.contained_secret


def test_redacts_assigned_secrets_but_keeps_the_key_name() -> None:
    result = redact("password = hunter2supersecret")
    assert result.text == "password = [REDACTED:assigned_secret]"
    assert "assigned_secret" in result.hits


def test_email_is_redacted_without_marking_a_secret() -> None:
    result = redact("contact alice@example.com for details")
    assert result.text == "contact [REDACTED:email] for details"
    assert result.contained_secret is False


def test_redaction_is_idempotent() -> None:
    once = redact("Authorization: bearer abcdefghijklmnop").text
    twice = redact(once).text
    assert once == twice


def test_redact_value_walks_nested_structures() -> None:
    value = {"a": ["ghp_abcdefghijklmnopqrstuvwxyz0123"], "b": {"c": "alice@example.com"}}
    cleaned = redact_value(value)
    assert "ghp_" not in str(cleaned)
    assert "alice@example.com" not in str(cleaned)


def test_contains_secret_ignores_plain_email() -> None:
    assert not contains_secret("mail me at alice@example.com")
    assert not contains_secret("just a normal sentence")
    assert contains_secret("token=abcdef123456")
