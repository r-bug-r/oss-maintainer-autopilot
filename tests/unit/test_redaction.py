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


