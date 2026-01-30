"""Tests for the output redactor."""

from mcp_hangar.domain.security.redactor import OutputRedactor


class TestOutputRedactor:
    """Tests for OutputRedactor."""

    def test_redact_known_secret(self):
        """Test redacting a known secret value."""
        redactor = OutputRedactor(known_secrets={"api_key": "super_secret_123"})
        text = "The API key is super_secret_123"
        result = redactor.redact(text)
        assert "super_secret_123" not in result
        assert "[REDACTED:api_key]" in result

    def test_redact_stripe_live_key(self):
        """Test redacting Stripe live key."""
        redactor = OutputRedactor()
        text = "Using key: sk_live_abc123def456ghi789jkl012mno345pqr"
        result = redactor.redact(text)
        assert "sk_live_" not in result
        assert "[REDACTED:stripe_live_key]" in result

    def test_redact_stripe_test_key(self):
        """Test redacting Stripe test key."""
        redactor = OutputRedactor()
        text = "Test key: sk_test_abc123def456ghi789jkl012mno345pqr"
        result = redactor.redact(text)
        assert "sk_test_" not in result
        assert "[REDACTED:stripe_test_key]" in result

    def test_redact_github_pat(self):
        """Test redacting GitHub personal access token."""
        redactor = OutputRedactor()
        text = "Token: ghp_abc123def456ghi789jkl012mno345pqrstuvw"
        result = redactor.redact(text)
        assert "ghp_" not in result
        assert "[REDACTED:github_pat]" in result

    def test_redact_slack_token(self):
        """Test redacting Slack token."""
        redactor = OutputRedactor()
        text = "Slack: xoxb-1234567890-1234567890123-abc123def456ghi789"
        result = redactor.redact(text)
        assert "xoxb-" not in result
        assert "[REDACTED:slack_token]" in result

    def test_redact_bearer_token(self):
        """Test redacting Bearer token."""
        redactor = OutputRedactor()
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123"
        result = redactor.redact(text)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123" not in result

    def test_redact_aws_access_key(self):
        """Test redacting AWS access key."""
        redactor = OutputRedactor()
        text = "AWS key: AKIAIOSFODNN7EXAMPLE"
        result = redactor.redact(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED:aws_access_key]" in result

    def test_add_known_secret(self):
        """Test adding a known secret after initialization."""
        redactor = OutputRedactor()
        redactor.add_known_secret("my_secret", "secret_value_here")
        text = "The value is secret_value_here"
        result = redactor.redact(text)
        assert "secret_value_here" not in result
        assert "[REDACTED:my_secret]" in result

    def test_add_custom_pattern(self):
        """Test adding a custom redaction pattern."""
        redactor = OutputRedactor()
        redactor.add_pattern(r"custom_\d+", "custom_pattern")
        text = "Found: custom_12345"
        result = redactor.redact(text)
        assert "custom_12345" not in result
        assert "[REDACTED:custom_pattern]" in result

    def test_is_sensitive_with_known_secret(self):
        """Test is_sensitive detects known secrets."""
        redactor = OutputRedactor(known_secrets={"api_key": "secret123"})
        assert redactor.is_sensitive("Contains secret123") is True
        assert redactor.is_sensitive("No secrets here") is False

    def test_is_sensitive_with_pattern(self):
        """Test is_sensitive detects pattern matches."""
        redactor = OutputRedactor()
        assert redactor.is_sensitive("Key: sk_live_abc123def456ghi789jkl012") is True
        assert redactor.is_sensitive("No sensitive data") is False

    def test_empty_text(self):
        """Test redacting empty text."""
        redactor = OutputRedactor()
        assert redactor.redact("") == ""
        assert redactor.redact(None) is None

    def test_multiple_secrets_in_text(self):
        """Test redacting multiple secrets in same text."""
        redactor = OutputRedactor(
            known_secrets={
                "key1": "secret1",
                "key2": "secret2",
            }
        )
        text = "First: secret1, Second: secret2"
        result = redactor.redact(text)
        assert "secret1" not in result
        assert "secret2" not in result
        assert "[REDACTED:key1]" in result
        assert "[REDACTED:key2]" in result

    def test_redact_long_strings_disabled(self):
        """Test disabling long string redaction."""
        redactor = OutputRedactor(redact_long_strings=False)
        text = "Long string: " + "a" * 40
        result = redactor.redact(text)
        assert "a" * 40 in result

    def test_does_not_redact_code_like_strings(self):
        """Test code-like strings are not redacted."""
        redactor = OutputRedactor()
        text = "Function: get_user_by_id_and_email_address"
        result = redactor.redact(text)
        assert "get_user_by_id_and_email_address" in result

    def test_does_not_redact_test_names(self):
        """Test test names are not redacted."""
        redactor = OutputRedactor()
        text = "Running: test_authentication_works_correctly"
        result = redactor.redact(text)
        assert "test_authentication_works_correctly" in result

    def test_short_secrets_not_added(self):
        """Test very short secrets are not added."""
        redactor = OutputRedactor()
        redactor.add_known_secret("short", "abc")
        text = "Contains abc in text"
        result = redactor.redact(text)
        assert "abc" in result

    def test_custom_replacement(self):
        """Test custom replacement string."""
        redactor = OutputRedactor()
        redactor.add_pattern(r"password=\w+", "password", replacement="password=***")
        text = "Config: password=secret123"
        result = redactor.redact(text)
        assert result == "Config: password=***"
