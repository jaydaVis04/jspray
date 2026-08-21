from __future__ import annotations

from jayspray.logging import redact, redact_text


def test_redacts_sensitive_values_in_free_form_text_and_nested_data() -> None:
    message = "token=private Authorization: Bearer abc.def https://user:pass@example.test/x"
    result = redact_text(message)
    assert "private" not in result
    assert "abc.def" not in result
    assert "user:pass" not in result
    assert result.count("[REDACTED]") >= 3
    assert redact({"error": "cookie=session-value", "nonce": "value"}) == {
        "error": "cookie=[REDACTED]",
        "nonce": "[REDACTED]",
    }
