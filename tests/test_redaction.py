from openminion.base.redaction import redact_mapping, redact_sensitive_text


def test_redact_sensitive_text_masks_openai_and_bearer_tokens() -> None:
    input_text = (
        "Authorization: Bearer tok_1234567890 and key sk-test12345678901234567890"
    )
    output, count = redact_sensitive_text(input_text)
    assert count >= 2
    assert "tok_1234567890" not in output
    assert "sk-test12345678901234567890" not in output
    assert "[REDACTED]" in output


def test_redact_mapping_masks_sensitive_keys_and_nested_values() -> None:
    payload = {
        "api_key": "abc123",
        "details": {
            "note": "token=my-secret-token",
        },
        "list": [
            "sk-test12345678901234567890",
            {"authorization": "Bearer xxxxxxxxxxxxxxxx"},
        ],
    }
    redacted, count = redact_mapping(payload)
    assert count >= 3
    assert redacted["api_key"] == "[REDACTED]"
    assert "[REDACTED]" in redacted["details"]["note"]
    assert "[REDACTED]" in redacted["list"][0]
    assert redacted["list"][1]["authorization"] == "[REDACTED]"
