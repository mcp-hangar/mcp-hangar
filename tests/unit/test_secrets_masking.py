"""Secret masking and the scrubbed environment handed to a launched provider."""

import os
import re
from unittest.mock import patch

from mcp_hangar.domain.security.secrets import (  # noqa: E402
    SecretsMask,
    SecureEnvironment,
    create_secure_env_for_provider,
    is_sensitive_key,
    mask_sensitive_value,
    redact_secrets_in_string,
)


class TestMaskSensitiveValueExtended:
    """Extended tests for mask_sensitive_value edge cases."""

    def test_empty_value(self):
        assert mask_sensitive_value("") == ""

    def test_short_value_fully_masked(self):
        result = mask_sensitive_value("short")
        assert "*" in result
        assert "short" not in result

    def test_max_visible_reduction(self):
        """When visible_prefix + visible_suffix > max_visible, both are reduced."""
        result = mask_sensitive_value(
            "a_long_secret_value_here",
            visible_prefix=10,
            visible_suffix=10,
            max_visible=8,
        )
        assert "*" in result

    def test_value_shorter_than_total_visible_plus_min_mask(self):
        """When value length <= total_visible + min_mask_length, prefix is reduced."""
        result = mask_sensitive_value(
            "short_val",
            visible_prefix=4,
            visible_suffix=0,
            min_mask_length=8,
        )
        assert "*" in result

    def test_visible_suffix(self):
        result = mask_sensitive_value(
            "secretpassword12345678",
            visible_prefix=4,
            visible_suffix=2,
        )
        assert result.startswith("secr")
        assert result.endswith("78")
        assert "*" in result


class TestSecretsMaskExtended:
    """Extended tests for SecretsMask."""

    def test_is_sensitive_with_safe_keys(self):
        mask = SecretsMask(safe_keys={"PASSWORD"})
        assert not mask.is_sensitive("PASSWORD")

    def test_is_sensitive_with_additional_keys(self):
        mask = SecretsMask(additional_keys={"CUSTOM_SENSITIVE"})
        assert mask.is_sensitive("custom_sensitive")

    def test_is_sensitive_with_additional_patterns(self):
        mask = SecretsMask(additional_patterns=[re.compile(r"(?i)my_custom")])
        assert mask.is_sensitive("MY_CUSTOM_VAR")

    def test_mask_method(self):
        mask = SecretsMask()
        result = mask.mask("secretpassword123")
        assert "*" in result

    def test_mask_dict_recursive(self):
        mask = SecretsMask()
        data = {
            "outer": {
                "password": "secret123",
                "normal": "value",
            }
        }
        result = mask.mask_dict(data, recursive=True)
        assert result["outer"]["normal"] == "value"
        assert "*" in result["outer"]["password"]

    def test_mask_dict_non_recursive(self):
        mask = SecretsMask()
        data = {
            "password": "secret123",
            "nested": {"password": "inner_secret"},
        }
        result = mask.mask_dict(data, recursive=False)
        assert "*" in result["password"]
        # Nested dict is kept as-is since recursive=False
        assert isinstance(result["nested"], dict)

    def test_mask_dict_non_string_value(self):
        mask = SecretsMask()
        data = {"password": 12345, "count": 10}
        result = mask.mask_dict(data)
        assert result["password"] == 12345  # not string, not masked
        assert result["count"] == 10


class TestSecureEnvironmentExtended:
    """Extended tests for SecureEnvironment methods."""

    def test_set_and_get(self):
        env = SecureEnvironment({"A": "1"})
        env.set("B", "2")
        assert env.get("B") == "2"

    def test_unset(self):
        env = SecureEnvironment({"A": "1"})
        env.unset("A")
        assert env.get("A") is None

    def test_unset_nonexistent_key(self):
        env = SecureEnvironment({})
        env.unset("MISSING")  # should not raise

    def test_get_default(self):
        env = SecureEnvironment({})
        assert env.get("MISSING", default="fallback") == "fallback"

    def test_get_masked_returns_none_for_missing(self):
        env = SecureEnvironment({})
        assert env.get_masked("MISSING") is None

    def test_get_masked_non_sensitive_key(self):
        env = SecureEnvironment({"MY_CONFIG": "value"})
        assert env.get_masked("MY_CONFIG") == "value"

    def test_to_dict_unmasked(self):
        env = SecureEnvironment({"PASSWORD": "secret", "PATH": "/usr"})
        result = env.to_dict(mask_sensitive=False)
        assert result["PASSWORD"] == "secret"
        assert result["PATH"] == "/usr"

    def test_to_subprocess_env_include_parent(self):
        env = SecureEnvironment({"CUSTOM": "val"})
        result = env.to_subprocess_env(include_parent=True)
        # Should include CUSTOM and parent env vars
        assert result["CUSTOM"] == "val"
        assert "PATH" in result  # from os.environ

    def test_to_subprocess_env_no_parent(self):
        env = SecureEnvironment({"CUSTOM": "val"})
        result = env.to_subprocess_env(include_parent=False)
        assert result == {"CUSTOM": "val"}

    def test_to_subprocess_env_whitelist(self):
        env = SecureEnvironment({"A": "1", "B": "2", "C": "3"})
        result = env.to_subprocess_env(include_parent=False, whitelist={"A", "C"})
        assert "A" in result
        assert "C" in result
        assert "B" not in result

    def test_to_subprocess_env_blacklist(self):
        env = SecureEnvironment({"A": "1", "B": "2", "C": "3"})
        result = env.to_subprocess_env(include_parent=False, blacklist={"B"})
        assert "A" in result
        assert "C" in result
        assert "B" not in result

    def test_validate_all_present(self):
        env = SecureEnvironment({"A": "1", "B": "2"})
        missing = env.validate(["A", "B"])
        assert missing == []

    def test_validate_some_missing(self):
        env = SecureEnvironment({"A": "1"})
        missing = env.validate(["A", "B", "C"])
        assert "B" in missing
        assert "C" in missing

    def test_validate_empty_value_counts_as_missing(self):
        env = SecureEnvironment({"A": ""})
        missing = env.validate(["A"])
        assert "A" in missing

    def test_accessed_keys_tracking(self):
        env = SecureEnvironment({"A": "1", "B": "2"})
        env.get("A")
        env.get("C")  # missing key still tracked
        assert env.accessed_keys == {"A", "C"}

    def test_contains(self):
        env = SecureEnvironment({"A": "1"})
        assert "A" in env
        assert "B" not in env

    def test_repr(self):
        env = SecureEnvironment({"A": "1", "B": "2"})
        r = repr(env)
        assert "2 variables" in r
        assert "SecureEnvironment" in r

    def test_defaults_to_os_environ(self):
        env = SecureEnvironment()
        assert "PATH" in env


class TestIsSensitiveKeyExtended:
    """Edge case tests for is_sensitive_key."""

    def test_empty_key(self):
        assert not is_sensitive_key("")

    def test_case_insensitive_exact(self):
        assert is_sensitive_key("password")
        assert is_sensitive_key("Password")
        assert is_sensitive_key("PASSWORD")

    def test_pattern_match_suffix(self):
        assert is_sensitive_key("my_custom_token")
        assert is_sensitive_key("database_key")
        assert is_sensitive_key("app_secret")


class TestRedactSecretsInStringExtended:
    """Extended tests for redact_secrets_in_string."""

    def test_basic_auth_in_url(self):
        text = "Connect to http://user:password123@host.com/db"
        result = redact_secrets_in_string(text)
        assert "password123" not in result
        assert "[REDACTED]" in result

    def test_aws_key_pattern(self):
        text = "Key is AKIAIOSFODNN7EXAMPLE1"
        result = redact_secrets_in_string(text)
        assert "AKIAIOSFODNN7EXAMPLE1" not in result

    def test_private_key_header(self):
        text = "Found: -----BEGIN RSA PRIVATE KEY-----"
        result = redact_secrets_in_string(text)
        assert "PRIVATE KEY" not in result

    def test_custom_patterns(self):
        custom = [re.compile(r"custom_\d+")]
        text = "ID: custom_12345"
        result = redact_secrets_in_string(text, patterns=custom)
        assert "custom_12345" not in result

    def test_no_secrets_returns_original(self):
        text = "This is a normal log message"
        result = redact_secrets_in_string(text)
        assert result == text


class TestCreateSecureEnvExtended:
    """Extended tests for create_secure_env_for_provider."""

    def test_with_base_env(self):
        with patch.dict(os.environ, {"PATH": "/usr"}, clear=True):
            env = create_secure_env_for_provider(
                base_env={"BASE_VAR": "base_val"},
            )
            d = env.to_dict(mask_sensitive=False)
            assert d["BASE_VAR"] == "base_val"

    def test_with_provider_env_overrides(self):
        with patch.dict(os.environ, {"PATH": "/usr"}, clear=True):
            env = create_secure_env_for_provider(
                base_env={"KEY": "base"},
                mcp_server_env={"KEY": "provider"},
            )
            d = env.to_dict(mask_sensitive=False)
            assert d["KEY"] == "provider"

    def test_no_inherit_parent(self):
        with patch.dict(os.environ, {"PATH": "/usr", "HOME": "/home"}, clear=True):
            env = create_secure_env_for_provider(
                inherit_parent=False,
                mcp_server_env={"MY_VAR": "val"},
            )
            d = env.to_dict(mask_sensitive=False)
            assert "PATH" not in d
            assert d["MY_VAR"] == "val"

    def test_sensitive_filter_disabled(self):
        with patch.dict(
            os.environ,
            {"PATH": "/usr", "PASSWORD": "secret"},
            clear=True,
        ):
            env = create_secure_env_for_provider(sensitive_key_filter=False)
            d = env.to_dict(mask_sensitive=False)
            assert "PASSWORD" in d
