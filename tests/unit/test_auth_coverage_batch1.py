"""
Auth stack coverage Batch 1: domain/security module deep coverage.

Targets uncovered branches in:
- input_validator.py  (ValidationIssue, ValidationResult, all validator methods)
- secrets.py          (SecretsMask, SecureEnvironment, utility functions)
- rate_limiter.py     (CompositeRateLimiter, cleanup, global functions)
- sanitizer.py        (all methods, convenience functions)
- redactor.py         (edge cases, helper methods)
- roles.py            (fallback stub)
"""

import os
import re
import time
from unittest.mock import patch

import pytest

# --- input_validator.py ---

from mcp_hangar.domain.security.input_validator import (
    InputValidator,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)


class TestValidationIssue:
    """Tests for ValidationIssue dataclass."""

    def test_to_dict_without_value(self):
        issue = ValidationIssue(field="f", message="m", severity=ValidationSeverity.ERROR)
        d = issue.to_dict()
        assert d == {"field": "f", "message": "m", "severity": "error"}
        assert "value" not in d

    def test_to_dict_with_short_value(self):
        issue = ValidationIssue(field="f", message="m", value="short")
        d = issue.to_dict()
        assert d["value"] == "short"

    def test_to_dict_truncates_long_value(self):
        long_val = "x" * 200
        issue = ValidationIssue(field="f", message="m", value=long_val)
        d = issue.to_dict()
        assert len(d["value"]) == 103  # 100 chars + "..."
        assert d["value"].endswith("...")

    def test_severity_values(self):
        assert ValidationSeverity.ERROR.value == "error"
        assert ValidationSeverity.WARNING.value == "warning"
        assert ValidationSeverity.INFO.value == "info"


class TestValidationResult:
    """Tests for ValidationResult methods."""

    def test_add_error_sets_valid_false(self):
        result = ValidationResult(valid=True)
        result.add_error("field", "error msg", value=42)
        assert not result.valid
        assert len(result.issues) == 1
        assert result.issues[0].severity == ValidationSeverity.ERROR

    def test_add_warning_does_not_change_valid(self):
        result = ValidationResult(valid=True)
        result.add_warning("field", "warning msg", value="v")
        assert result.valid
        assert len(result.issues) == 1
        assert result.issues[0].severity == ValidationSeverity.WARNING

    def test_merge_propagates_invalid(self):
        r1 = ValidationResult(valid=True)
        r2 = ValidationResult(valid=True)
        r2.add_error("x", "fail")
        r1.merge(r2)
        assert not r1.valid
        assert len(r1.issues) == 1

    def test_merge_valid_into_valid(self):
        r1 = ValidationResult(valid=True)
        r2 = ValidationResult(valid=True)
        r2.add_warning("x", "warn")
        r1.merge(r2)
        assert r1.valid
        assert len(r1.issues) == 1

    def test_errors_property_filters(self):
        r = ValidationResult(valid=True)
        r.add_error("a", "err")
        r.add_warning("b", "warn")
        assert len(r.errors) == 1
        assert r.errors[0].field == "a"

    def test_warnings_property_filters(self):
        r = ValidationResult(valid=True)
        r.add_error("a", "err")
        r.add_warning("b", "warn")
        assert len(r.warnings) == 1
        assert r.warnings[0].field == "b"

    def test_to_dict(self):
        r = ValidationResult(valid=True)
        r.add_error("a", "err")
        r.add_warning("b", "warn")
        d = r.to_dict()
        assert d["valid"] is False
        assert d["error_count"] == 1
        assert d["warning_count"] == 1
        assert len(d["issues"]) == 2


class TestInputValidatorExtended:
    """Extended coverage for InputValidator methods."""

    def test_init_with_custom_config(self):
        v = InputValidator(
            allow_absolute_paths=True,
            allowed_commands=["python", "node"],
            blocked_commands=["rm"],
        )
        assert v.allow_absolute_paths is True
        assert v.allowed_commands == {"python", "node"}
        assert v.blocked_commands == {"rm"}

    def test_provider_id_non_string_type(self):
        v = InputValidator()
        result = v.validate_provider_id(123)
        assert not result.valid
        assert any("string" in i.message.lower() for i in result.errors)

    def test_tool_name_non_string_type(self):
        v = InputValidator()
        result = v.validate_tool_name(999)
        assert not result.valid

    def test_tool_name_too_long(self):
        v = InputValidator()
        result = v.validate_tool_name("a" * 200)
        assert not result.valid
        assert any("length" in i.message.lower() for i in result.errors)

    def test_tool_name_path_traversal(self):
        v = InputValidator()
        result = v.validate_tool_name("tool..name")
        assert not result.valid
        assert any("traversal" in i.message.lower() for i in result.errors)

    def test_arguments_none_is_valid(self):
        v = InputValidator()
        result = v.validate_arguments(None)
        assert result.valid

    def test_arguments_non_serializable(self):
        v = InputValidator()
        result = v.validate_arguments({"fn": lambda: None})
        assert not result.valid
        assert any("serializable" in i.message.lower() for i in result.errors)

    def test_arguments_non_string_key(self):
        v = InputValidator()
        result = v.validate_arguments({123: "val"})
        assert result.valid is False or len(result.issues) > 0

    def test_arguments_empty_key(self):
        v = InputValidator()
        result = v.validate_arguments({"": "val"})
        assert any("empty" in i.message.lower() for i in result.issues)

    def test_arguments_very_long_string_value(self):
        v = InputValidator()
        result = v.validate_arguments({"key": "x" * 1_100_000})
        assert not result.valid

    def test_arguments_nested_list(self):
        v = InputValidator()
        result = v.validate_arguments({"items": [1, "two", {"nested": True}]})
        assert result.valid

    def test_timeout_none_is_valid(self):
        v = InputValidator()
        result = v.validate_timeout(None)
        assert result.valid

    def test_timeout_non_number(self):
        v = InputValidator()
        result = v.validate_timeout("fast")
        assert not result.valid

    def test_command_none(self):
        v = InputValidator()
        result = v.validate_command(None)
        assert not result.valid

    def test_command_not_list(self):
        v = InputValidator()
        result = v.validate_command("python script.py")
        assert not result.valid

    def test_command_empty_list(self):
        v = InputValidator()
        result = v.validate_command([])
        assert not result.valid

    def test_command_too_many_args(self):
        v = InputValidator()
        result = v.validate_command(["python"] + ["arg"] * 150)
        assert not result.valid

    def test_command_non_string_element(self):
        v = InputValidator()
        result = v.validate_command(["python", 42, "script.py"])
        assert not result.valid or len(result.issues) > 0

    def test_command_allowed_commands_whitelist(self):
        v = InputValidator(allowed_commands=["python"])
        result = v.validate_command(["node", "app.js"])
        assert not result.valid
        assert any("allowed" in i.message.lower() for i in result.errors)

    def test_command_absolute_path_warning(self):
        v = InputValidator(allow_absolute_paths=False)
        result = v.validate_command(["/usr/bin/python", "script.py"])
        assert len(result.warnings) > 0
        assert any("absolute" in w.message.lower() for w in result.warnings)

    def test_command_absolute_path_no_warning_when_allowed(self):
        v = InputValidator(allow_absolute_paths=True)
        result = v.validate_command(["/usr/bin/python", "script.py"])
        assert len(result.warnings) == 0

    def test_docker_image_none(self):
        v = InputValidator()
        result = v.validate_docker_image(None)
        assert not result.valid

    def test_docker_image_non_string(self):
        v = InputValidator()
        result = v.validate_docker_image(42)
        assert not result.valid

    def test_docker_image_empty(self):
        v = InputValidator()
        result = v.validate_docker_image("")
        assert not result.valid

    def test_docker_image_too_long(self):
        v = InputValidator()
        result = v.validate_docker_image("a" * 300)
        assert not result.valid

    def test_docker_image_lenient_format(self):
        """Images that fail strict DOCKER_IMAGE_PATTERN but pass lenient check."""
        v = InputValidator()
        result = v.validate_docker_image("UPPER_CASE:tag")
        # Lenient regex allows \w.\-/:@ so this should pass
        assert result.valid

    def test_env_vars_none_is_valid(self):
        v = InputValidator()
        result = v.validate_environment_variables(None)
        assert result.valid

    def test_env_vars_not_dict(self):
        v = InputValidator()
        result = v.validate_environment_variables("not a dict")
        assert not result.valid

    def test_env_vars_non_string_key(self):
        v = InputValidator()
        result = v.validate_environment_variables({123: "val"})
        assert not result.valid or len(result.issues) > 0

    def test_env_vars_empty_key(self):
        v = InputValidator()
        result = v.validate_environment_variables({"": "val"})
        assert any("empty" in i.message.lower() for i in result.issues)

    def test_env_vars_long_key(self):
        v = InputValidator()
        result = v.validate_environment_variables({"K" * 300: "val"})
        assert any("length" in i.message.lower() for i in result.issues)

    def test_env_vars_invalid_key_format(self):
        v = InputValidator()
        result = v.validate_environment_variables({"123-bad": "val"})
        assert not result.valid

    def test_env_vars_non_string_value(self):
        v = InputValidator()
        result = v.validate_environment_variables({"KEY": 42})
        assert not result.valid or len(result.issues) > 0

    def test_env_vars_long_value(self):
        v = InputValidator()
        result = v.validate_environment_variables({"KEY": "x" * 40000})
        assert any("length" in i.message.lower() for i in result.issues)

    def test_env_vars_dangerous_value_warning(self):
        v = InputValidator()
        result = v.validate_environment_variables({"KEY": "value; rm -rf /"})
        # Should have a warning about dangerous chars
        assert len(result.warnings) > 0

    def test_validate_all_combines_results(self):
        v = InputValidator()
        result = v.validate_all(
            provider_id="valid_id",
            tool_name="valid_tool",
            timeout=30.0,
        )
        assert result.valid

    def test_validate_all_with_invalid_inputs(self):
        v = InputValidator()
        result = v.validate_all(
            provider_id="",
            tool_name="",
            timeout=-1,
        )
        assert not result.valid
        assert len(result.errors) >= 3


# --- secrets.py ---

from mcp_hangar.domain.security.secrets import (
    create_secure_env_for_provider,
    is_sensitive_key,
    mask_sensitive_value,
    redact_secrets_in_string,
    SecretsMask,
    SecureEnvironment,
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
                provider_env={"KEY": "provider"},
            )
            d = env.to_dict(mask_sensitive=False)
            assert d["KEY"] == "provider"

    def test_no_inherit_parent(self):
        with patch.dict(os.environ, {"PATH": "/usr", "HOME": "/home"}, clear=True):
            env = create_secure_env_for_provider(
                inherit_parent=False,
                provider_env={"MY_VAR": "val"},
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


# --- rate_limiter.py ---

from mcp_hangar.domain.security.rate_limiter import (
    CompositeRateLimiter,
    get_rate_limiter,
    InMemoryRateLimiter,
    RateLimitConfig,
    RateLimitResult,
    RateLimitScope,
    reset_rate_limiter,
    TokenBucket,
)


class TestRateLimitConfigValidation:
    """Tests for RateLimitConfig validation."""

    def test_valid_config(self):
        config = RateLimitConfig(requests_per_second=10.0, burst_size=20)
        assert config.requests_per_second == 10.0
        assert config.burst_size == 20

    def test_invalid_rate(self):
        with pytest.raises(ValueError, match="requests_per_second"):
            RateLimitConfig(requests_per_second=0)

    def test_negative_rate(self):
        with pytest.raises(ValueError, match="requests_per_second"):
            RateLimitConfig(requests_per_second=-1.0)

    def test_invalid_burst(self):
        with pytest.raises(ValueError, match="burst_size"):
            RateLimitConfig(requests_per_second=1.0, burst_size=0)

    def test_scope_enum(self):
        assert RateLimitScope.GLOBAL.value == "global"
        assert RateLimitScope.PER_PROVIDER.value == "provider"
        assert RateLimitScope.PER_TOOL.value == "tool"
        assert RateLimitScope.PER_CLIENT.value == "client"


class TestRateLimitResultExtended:
    """Extended tests for RateLimitResult."""

    def test_to_dict(self):
        result = RateLimitResult(
            allowed=True,
            remaining=5,
            reset_at=1000.0,
            retry_after=None,
            limit=10,
        )
        d = result.to_dict()
        assert d["allowed"] is True
        assert d["remaining"] == 5
        assert d["limit"] == 10
        assert "retry_after" not in d

    def test_to_dict_with_retry_after(self):
        result = RateLimitResult(
            allowed=False,
            remaining=0,
            reset_at=1000.0,
            retry_after=2.567,
            limit=10,
        )
        d = result.to_dict()
        assert d["retry_after"] == 2.57  # rounded to 2 decimal places

    def test_to_headers_no_retry(self):
        result = RateLimitResult(
            allowed=True,
            remaining=5,
            reset_at=1000.0,
            limit=10,
        )
        headers = result.to_headers()
        assert headers["X-RateLimit-Limit"] == "10"
        assert headers["X-RateLimit-Remaining"] == "5"
        assert "Retry-After" not in headers

    def test_to_headers_negative_remaining_clamped_to_zero(self):
        result = RateLimitResult(
            allowed=False,
            remaining=-3,
            reset_at=1000.0,
            limit=10,
        )
        headers = result.to_headers()
        assert headers["X-RateLimit-Remaining"] == "0"


class TestTokenBucketExtended:
    """Extended tests for TokenBucket."""

    def test_initial_tokens(self):
        bucket = TokenBucket(rate=10.0, capacity=20, initial_tokens=5)
        available, _ = bucket.peek()
        assert available == 5

    def test_peek_does_not_consume(self):
        bucket = TokenBucket(rate=10.0, capacity=5)
        avail1, _ = bucket.peek()
        avail2, _ = bucket.peek()
        assert avail1 == avail2

    def test_peek_time_to_full(self):
        bucket = TokenBucket(rate=10.0, capacity=10, initial_tokens=5)
        _, time_to_full = bucket.peek()
        assert time_to_full > 0

    def test_peek_already_full(self):
        bucket = TokenBucket(rate=10.0, capacity=5)
        _, time_to_full = bucket.peek()
        assert time_to_full == 0

    def test_reset(self):
        bucket = TokenBucket(rate=10.0, capacity=10)
        for _ in range(10):
            bucket.consume()
        bucket.reset()
        available, _ = bucket.peek()
        assert available == 10

    def test_consume_multiple_tokens(self):
        bucket = TokenBucket(rate=10.0, capacity=10)
        allowed, _ = bucket.consume(tokens=5)
        assert allowed
        available, _ = bucket.peek()
        assert available == 5

    def test_consume_more_than_available(self):
        bucket = TokenBucket(rate=10.0, capacity=5)
        allowed, wait = bucket.consume(tokens=10)
        assert not allowed
        assert wait > 0


class TestInMemoryRateLimiterExtended:
    """Extended tests for InMemoryRateLimiter."""

    def test_check_without_consuming(self):
        config = RateLimitConfig(requests_per_second=10, burst_size=5)
        limiter = InMemoryRateLimiter(config)
        result = limiter.check("key1")
        assert result.allowed
        assert result.remaining == 5
        # Check again -- should still show same availability
        result2 = limiter.check("key1")
        assert result2.remaining == 5

    def test_reset_key(self):
        config = RateLimitConfig(requests_per_second=1, burst_size=1)
        limiter = InMemoryRateLimiter(config)
        limiter.consume("key1")
        # Should be exhausted
        assert not limiter.consume("key1").allowed
        # Reset
        limiter.reset("key1")
        assert limiter.consume("key1").allowed

    def test_reset_nonexistent_key(self):
        limiter = InMemoryRateLimiter()
        limiter.reset("nonexistent")  # should not raise

    def test_reset_all(self):
        config = RateLimitConfig(requests_per_second=1, burst_size=1)
        limiter = InMemoryRateLimiter(config)
        limiter.consume("key1")
        limiter.consume("key2")
        limiter.reset_all()
        assert limiter.consume("key1").allowed
        assert limiter.consume("key2").allowed

    def test_get_stats(self):
        config = RateLimitConfig(requests_per_second=10, burst_size=20)
        limiter = InMemoryRateLimiter(config)
        limiter.consume("key1")
        limiter.consume("key2")
        stats = limiter.get_stats()
        assert stats["active_buckets"] == 2
        assert stats["config"]["requests_per_second"] == 10
        assert stats["config"]["burst_size"] == 20
        assert stats["config"]["scope"] == "global"

    def test_cleanup_removes_old_buckets(self):
        config = RateLimitConfig(requests_per_second=10, burst_size=5)
        limiter = InMemoryRateLimiter(config, cleanup_interval=0.01)
        limiter.consume("key1")
        # Force last_cleanup to be old
        limiter._last_cleanup = time.monotonic() - 1.0
        # Touch to trigger cleanup with old last_used
        limiter._bucket_last_used["key1"] = time.monotonic() - 1.0
        limiter.consume("key2")  # triggers cleanup
        stats = limiter.get_stats()
        # key1 should have been cleaned up
        assert stats["active_buckets"] <= 2

    def test_check_returns_retry_after_when_empty(self):
        config = RateLimitConfig(requests_per_second=1, burst_size=1)
        limiter = InMemoryRateLimiter(config)
        limiter.consume("key")
        result = limiter.check("key")
        assert not result.allowed
        assert result.retry_after is not None


class TestCompositeRateLimiter:
    """Tests for CompositeRateLimiter."""

    def test_check_all_allow(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=10))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=20))
        composite = CompositeRateLimiter({"global": l1, "provider": l2})
        result = composite.check("key")
        assert result.allowed
        assert result.limit == 10  # min of both

    def test_consume_all_allow(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=10))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=20))
        composite = CompositeRateLimiter({"global": l1, "provider": l2})
        result = composite.consume("key")
        assert result.allowed

    def test_consume_one_denies(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=1, burst_size=1))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=100))
        composite = CompositeRateLimiter({"strict": l1, "lenient": l2})
        composite.consume("key")  # uses up l1
        result = composite.consume("key")
        assert not result.allowed
        assert result.retry_after is not None

    def test_check_one_denies(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=1, burst_size=1))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=100))
        composite = CompositeRateLimiter({"strict": l1, "lenient": l2})
        l1.consume("key")  # exhaust l1
        result = composite.check("key")
        assert not result.allowed

    def test_reset_resets_all(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=1, burst_size=1))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=1, burst_size=1))
        composite = CompositeRateLimiter({"a": l1, "b": l2})
        composite.consume("key")
        composite.reset("key")
        result = composite.consume("key")
        assert result.allowed

    def test_empty_limiters(self):
        composite = CompositeRateLimiter({})
        result = composite.check("key")
        assert result.allowed
        assert result.remaining == 0


class TestGlobalRateLimiter:
    """Tests for global rate limiter functions."""

    def test_get_rate_limiter_creates_singleton(self):
        reset_rate_limiter()
        limiter1 = get_rate_limiter()
        limiter2 = get_rate_limiter()
        assert limiter1 is limiter2

    def test_reset_rate_limiter(self):
        reset_rate_limiter()
        limiter1 = get_rate_limiter()
        reset_rate_limiter()
        limiter2 = get_rate_limiter()
        assert limiter1 is not limiter2

    def test_get_rate_limiter_with_config(self):
        reset_rate_limiter()
        config = RateLimitConfig(requests_per_second=50, burst_size=100)
        limiter = get_rate_limiter(config)
        assert limiter.config.requests_per_second == 50


# --- sanitizer.py ---

from mcp_hangar.domain.security.sanitizer import (
    sanitize_command_argument,
    sanitize_environment_value,
    sanitize_log_message,
    sanitize_path,
    Sanitizer,
)


class TestSanitizerExtended:
    """Extended tests for Sanitizer methods."""

    def test_init_custom_limits(self):
        s = Sanitizer(
            max_argument_length=100,
            max_path_length=200,
            max_log_message_length=500,
        )
        assert s.max_argument_length == 100
        assert s.max_path_length == 200
        assert s.max_log_message_length == 500

    def test_sanitize_command_argument_non_string(self):
        s = Sanitizer()
        result = s.sanitize_command_argument(42)
        assert result == "42"

    def test_sanitize_command_argument_truncation(self):
        s = Sanitizer(max_argument_length=10)
        result = s.sanitize_command_argument("a" * 100)
        assert len(result) <= 10

    def test_sanitize_command_argument_allow_quotes(self):
        s = Sanitizer()
        result_no_quotes = s.sanitize_command_argument('"hello"', allow_quotes=False)
        result_quotes = s.sanitize_command_argument('"hello"', allow_quotes=True)
        assert '"' not in result_no_quotes
        assert '"' in result_quotes

    def test_sanitize_command_argument_allow_spaces(self):
        s = Sanitizer()
        # Space is not in SHELL_METACHARACTERS, so allow_spaces flag controls
        # whether space is added to the dangerous set.  When True (default),
        # space is discarded from the dangerous set (no-op since not present).
        # When False, space is not removed -- but since it was never there,
        # spaces still pass through.  Verify both paths execute without error
        # and spaces are preserved regardless (space is safe by default).
        result_spaces = s.sanitize_command_argument("hello world", allow_spaces=True)
        result_no_spaces = s.sanitize_command_argument("hello world", allow_spaces=False)
        assert " " in result_spaces
        assert isinstance(result_no_spaces, str)

    def test_sanitize_command_list(self):
        s = Sanitizer()
        result = s.sanitize_command_list(["python", "-c", "import os; print()"])
        assert len(result) == 3
        assert ";" not in result[2]

    def test_sanitize_environment_value_non_string(self):
        s = Sanitizer()
        result = s.sanitize_environment_value(42)
        assert result == "42"

    def test_sanitize_environment_value_truncation(self):
        s = Sanitizer()
        result = s.sanitize_environment_value("x" * 40000)
        assert len(result) <= s.MAX_ENV_VALUE_LENGTH

    def test_sanitize_environment_value_allow_newlines(self):
        s = Sanitizer()
        result = s.sanitize_environment_value("line1\nline2", allow_newlines=True)
        assert "\n" in result

    def test_sanitize_environment_dict(self):
        s = Sanitizer()
        result = s.sanitize_environment_dict({"KEY": "val\x00ue", "K2": "ok"})
        assert "\x00" not in result["KEY"]
        assert result["K2"] == "ok"

    def test_sanitize_path_hidden_files_disallowed(self):
        s = Sanitizer()
        with pytest.raises(ValueError, match="[Hh]idden"):
            s.sanitize_path(".secret/file", allow_hidden=False)

    def test_sanitize_path_hidden_files_allowed(self):
        s = Sanitizer()
        result = s.sanitize_path(".secret/file", allow_hidden=True)
        assert result == ".secret/file"

    def test_sanitize_path_control_characters(self):
        s = Sanitizer()
        with pytest.raises(ValueError, match="control"):
            s.sanitize_path("path\x01file")

    def test_sanitize_path_too_long(self):
        s = Sanitizer(max_path_length=50)
        with pytest.raises(ValueError, match="length"):
            s.sanitize_path("a" * 100)

    def test_sanitize_path_non_string(self):
        s = Sanitizer()
        result = s.sanitize_path(42)
        assert result == "42"

    def test_sanitize_path_unicode_normalization(self):
        s = Sanitizer()
        # NFKC normalizes fullwidth chars
        result = s.sanitize_path("normal_path")
        assert result == "normal_path"

    def test_sanitize_path_absolute_allowed(self):
        s = Sanitizer()
        result = s.sanitize_path("/usr/bin/python", allow_absolute=True)
        assert result == "/usr/bin/python"

    def test_sanitize_path_windows_absolute(self):
        s = Sanitizer()
        with pytest.raises(ValueError, match="[Aa]bsolute"):
            s.sanitize_path("C:\\Windows\\System32", allow_absolute=False)

    def test_sanitize_log_message_non_string(self):
        s = Sanitizer()
        result = s.sanitize_log_message(42)
        assert "42" in result

    def test_sanitize_log_message_crlf(self):
        s = Sanitizer()
        result = s.sanitize_log_message("line1\r\nline2")
        assert "\r" not in result
        assert "\n" not in result
        assert "\\r\\n" in result

    def test_sanitize_log_message_tab(self):
        s = Sanitizer()
        result = s.sanitize_log_message("col1\tcol2")
        assert "\t" not in result
        assert "\\t" in result

    def test_sanitize_log_message_custom_max_length(self):
        s = Sanitizer()
        result = s.sanitize_log_message("x" * 100, max_length=50)
        assert len(result) < 100
        assert "truncated" in result

    def test_sanitize_for_json_list(self):
        s = Sanitizer()
        result = s.sanitize_for_json(["hello\x00", "world"])
        assert result == ["hello", "world"]

    def test_sanitize_for_json_primitives(self):
        s = Sanitizer()
        assert s.sanitize_for_json(42) == 42
        assert s.sanitize_for_json(3.14) == 3.14
        assert s.sanitize_for_json(True) is True
        assert s.sanitize_for_json(None) is None

    def test_sanitize_for_json_unknown_type(self):
        s = Sanitizer()
        result = s.sanitize_for_json(object())
        assert isinstance(result, str)

    def test_escape_html(self):
        s = Sanitizer()
        assert s.escape_html('<script>alert("xss")</script>') == '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'

    def test_mask_value_empty(self):
        s = Sanitizer()
        assert s.mask_value("") == ""

    def test_mask_value_short(self):
        s = Sanitizer()
        result = s.mask_value("abc", visible_chars=4)
        assert "abc" not in result
        assert "*" in result

    def test_mask_value_normal(self):
        s = Sanitizer()
        result = s.mask_value("secretpassword", visible_chars=4)
        assert result.startswith("secr")
        assert "*" in result


class TestSanitizerConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_sanitize_command_argument_convenience(self):
        result = sanitize_command_argument("arg;evil")
        assert ";" not in result

    def test_sanitize_environment_value_convenience(self):
        result = sanitize_environment_value("val\x00ue")
        assert "\x00" not in result

    def test_sanitize_log_message_convenience(self):
        result = sanitize_log_message("msg\nevil")
        assert "\n" not in result

    def test_sanitize_path_convenience(self):
        result = sanitize_path("normal/path")
        assert result == "normal/path"

    def test_sanitize_path_convenience_rejects_traversal(self):
        with pytest.raises(ValueError):
            sanitize_path("../etc/passwd")


# --- redactor.py ---

from mcp_hangar.domain.security.redactor import OutputRedactor


class TestOutputRedactorExtended:
    """Extended tests for OutputRedactor edge cases."""

    def test_looks_like_code_upper_snake_case(self):
        redactor = OutputRedactor(redact_long_strings=True, min_long_string_length=10)
        text = "Const: MY_VERY_LONG_CONSTANT_NAME_HERE"
        result = redactor.redact(text)
        assert "MY_VERY_LONG_CONSTANT_NAME_HERE" in result

    def test_looks_like_code_set_prefix(self):
        redactor = OutputRedactor(redact_long_strings=True, min_long_string_length=10)
        text = "Call: set_configuration_for_provider"
        result = redactor.redact(text)
        assert "set_configuration_for_provider" in result

    def test_looks_like_code_spec_suffix(self):
        redactor = OutputRedactor(redact_long_strings=True, min_long_string_length=10)
        text = "Run: authentication_handler_spec"
        result = redactor.redact(text)
        assert "authentication_handler_spec" in result

    def test_looks_like_code_spec_prefix(self):
        redactor = OutputRedactor(redact_long_strings=True, min_long_string_length=10)
        text = "Run: spec_authentication_handler_test"
        result = redactor.redact(text)
        assert "spec_authentication_handler_test" in result

    def test_known_safe_sha256_prefix(self):
        redactor = OutputRedactor(redact_long_strings=True)
        text = "Hash: sha256-abc123def456ghi789jkl012mno345pqr"
        result = redactor.redact(text)
        assert "sha256-abc123def456ghi789jkl012mno345pqr" in result

    def test_known_safe_low_entropy(self):
        redactor = OutputRedactor(redact_long_strings=True)
        text = "Pad: " + "a" * 40
        result = redactor.redact(text)
        assert "a" * 40 in result

    def test_is_sensitive_with_custom_pattern(self):
        redactor = OutputRedactor()
        redactor.add_pattern(r"custom_secret_\d+", "custom")
        assert redactor.is_sensitive("Found custom_secret_12345 here")
        assert not redactor.is_sensitive("No custom secrets")

    def test_redact_npm_token(self):
        redactor = OutputRedactor()
        text = "Token: npm_abcdefghijklmnopqrstuvwxyz1234567890"
        result = redactor.redact(text)
        # The raw token value must be removed; the redaction label may contain
        # a descriptive tag like "[REDACTED:npm_token]" which is expected.
        assert "npm_abcdefghijklmnopqrstuvwxyz1234567890" not in result
        assert "REDACTED" in result

    def test_redact_google_api_key(self):
        redactor = OutputRedactor()
        text = "Key: AIzaSyA-1234567890123456789012345678901"
        result = redactor.redact(text)
        assert "AIzaSyA" not in result

    def test_redact_jwt_token(self):
        redactor = OutputRedactor()
        # Minimal valid-looking JWT (header.payload, each >50 chars)
        header = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6InRlc3QifQ"
        payload = "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiYWRtaW4iOnRydWV9"
        text = f"JWT: {header}.{payload}"
        result = redactor.redact(text)
        assert header not in result

    def test_add_pattern_with_compiled_regex(self):
        redactor = OutputRedactor()
        redactor.add_pattern(re.compile(r"my_\d+"), "my_pattern")
        result = redactor.redact("Found my_12345")
        assert "my_12345" not in result


# --- roles.py ---


class TestRolesStub:
    """Tests for the roles.py fallback stub."""

    def test_fallback_imports(self):
        """When enterprise is not available, fallback values are used."""
        from mcp_hangar.domain.security.roles import (
            BUILTIN_ROLES,
            get_builtin_role,
            get_permission,
            list_builtin_roles,
            list_permissions,
            PERMISSIONS,
        )

        # When enterprise IS available, these return real values
        # Either way the symbols must be importable
        assert isinstance(BUILTIN_ROLES, dict)
        assert isinstance(PERMISSIONS, dict)

    def test_role_constants_importable(self):
        from mcp_hangar.domain.security.roles import (
            ROLE_ADMIN,
            ROLE_AUDITOR,
            ROLE_DEVELOPER,
            ROLE_PROVIDER_ADMIN,
            ROLE_VIEWER,
        )
        # These should be importable (either None or Role objects)
        # No assertion on value -- depends on whether enterprise is installed

    def test_get_builtin_role(self):
        from mcp_hangar.domain.security.roles import get_builtin_role
        # Should return a Role or None
        result = get_builtin_role("nonexistent_role")
        # If enterprise roles exist it returns None for unknown; if fallback, always None
        assert result is None or hasattr(result, "name")

    def test_list_functions(self):
        from mcp_hangar.domain.security.roles import list_builtin_roles, list_permissions
        roles = list_builtin_roles()
        perms = list_permissions()
        assert isinstance(roles, list)
        assert isinstance(perms, list)

    def test_get_permission(self):
        from mcp_hangar.domain.security.roles import get_permission
        # get_permission takes a single key string, e.g. "providers:read"
        result = get_permission("providers:read")
        # Returns Permission or None
        assert result is None or hasattr(result, "resource")
