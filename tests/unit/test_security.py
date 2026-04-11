"""
Security tests for the MCP Hangar.

Tests for:
- Input validation
- Command injection prevention
- Rate limiting
- Secrets management
- Security audit logging
"""

import os
import time
from unittest.mock import patch

import pytest

from mcp_hangar.application.event_handlers.security_handler import (
    InMemorySecuritySink,
    SecurityEvent,
    SecurityEventHandler,
    SecurityEventType,
    SecuritySeverity,
)
from mcp_hangar.domain.exceptions import ValidationError

# Import security modules
from mcp_hangar.domain.security.input_validator import (
    DANGEROUS_PATTERNS,
    InputValidator,
    validate_arguments,
    validate_command,
    validate_docker_image,
    validate_environment_variables,
    validate_provider_id,
    validate_timeout,
    validate_tool_name,
)
from mcp_hangar.domain.security.rate_limiter import InMemoryRateLimiter, RateLimitConfig, RateLimitResult, TokenBucket
from mcp_hangar.domain.security.sanitizer import (
    sanitize_command_argument,
    sanitize_environment_value,
    sanitize_log_message,
    Sanitizer,
)
from mcp_hangar.domain.security.secrets import (
    create_secure_env_for_provider,
    is_sensitive_key,
    mask_sensitive_value,
    redact_secrets_in_string,
    SecretsMask,
    SecureEnvironment,
)

# Mark all tests in this module as security tests
pytestmark = pytest.mark.security


class TestInputValidator:
    """Tests for input validation."""

    def test_valid_provider_id(self):
        """Test validation of valid provider IDs."""
        valid_ids = [
            "my_provider",
            "provider-1",
            "MyProvider123",
            "a",  # Single character
            "a" * 64,  # Max length
        ]
        for provider_id in valid_ids:
            result = validate_provider_id(provider_id)
            assert result.valid, f"Expected '{provider_id}' to be valid"

    def test_invalid_provider_id(self):
        """Test validation rejects invalid provider IDs."""
        invalid_ids = [
            "",  # Empty
            None,  # None
            "123provider",  # Starts with number
            "provider!",  # Special character
            "provider with spaces",
            "a" * 65,  # Too long
            "provider;rm -rf",  # Injection attempt
            "provider`id`",  # Backtick injection
            "provider$(whoami)",  # Command substitution
        ]
        for provider_id in invalid_ids:
            result = validate_provider_id(provider_id)
            assert not result.valid, f"Expected '{provider_id}' to be invalid"

    def test_valid_tool_name(self):
        """Test validation of valid tool names."""
        valid_names = [
            "my_tool",
            "tool.method",
            "namespace/tool",
            "MyTool_v2",
        ]
        for tool_name in valid_names:
            result = validate_tool_name(tool_name)
            assert result.valid, f"Expected '{tool_name}' to be valid"

    def test_invalid_tool_name(self):
        """Test validation rejects invalid tool names."""
        invalid_names = [
            "",
            None,
            "123tool",  # Starts with number
            "tool!",
            "../etc/passwd",  # Path traversal
            "tool;inject",
        ]
        for tool_name in invalid_names:
            result = validate_tool_name(tool_name)
            assert not result.valid, f"Expected '{tool_name}' to be invalid"

    def test_valid_arguments(self):
        """Test validation of valid arguments."""
        valid_args = [
            {},
            {"key": "value"},
            {"nested": {"key": "value"}},
            {"list": [1, 2, 3]},
            {"complex": {"a": [1, {"b": 2}]}},
        ]
        for args in valid_args:
            result = validate_arguments(args)
            assert result.valid, f"Expected {args} to be valid"

    def test_invalid_arguments(self):
        """Test validation rejects invalid arguments."""
        # Non-dict
        result = validate_arguments("not a dict")
        assert not result.valid

        # List instead of dict
        result = validate_arguments([1, 2, 3])
        assert not result.valid

    def test_arguments_size_limit(self):
        """Test arguments size limit enforcement."""
        # Create arguments that exceed size limit
        large_value = "x" * 2_000_000  # 2MB
        result = validate_arguments({"large": large_value})
        assert not result.valid
        assert any("size" in str(e.message).lower() for e in result.errors)

    def test_arguments_depth_limit(self):
        """Test arguments nesting depth limit."""
        # Create deeply nested structure
        deep = {}
        current = deep
        for _ in range(15):  # Exceed MAX_DEPTH of 10
            current["nested"] = {}
            current = current["nested"]

        result = validate_arguments(deep)
        assert not result.valid
        assert any("depth" in str(e.message).lower() for e in result.errors)

    def test_valid_timeout(self):
        """Test validation of valid timeouts."""
        valid_timeouts = [0.1, 1.0, 30.0, 300.0, 3600.0]
        for timeout in valid_timeouts:
            result = validate_timeout(timeout)
            assert result.valid, f"Expected {timeout} to be valid"

    def test_invalid_timeout(self):
        """Test validation rejects invalid timeouts."""
        invalid_timeouts = [
            0,  # Too small
            0.01,  # Below minimum
            -1,  # Negative
            3601,  # Above maximum
            "thirty",  # Not a number
        ]
        for timeout in invalid_timeouts:
            result = validate_timeout(timeout)
            assert not result.valid, f"Expected {timeout} to be invalid"

    def test_valid_command(self):
        """Test validation of valid commands."""
        valid_commands = [
            ["python", "-m", "mymodule"],
            ["python3", "script.py"],
            ["node", "app.js"],
        ]
        for cmd in valid_commands:
            result = validate_command(cmd)
            assert result.valid, f"Expected {cmd} to be valid"

    def test_command_injection_prevention(self):
        """Test that command injection attempts are blocked."""
        injection_attempts = [
            ["python", "-c", "import os; os.system('rm -rf /')"],
            ["python", "; rm -rf /"],
            ["python", "| cat /etc/passwd"],
            ["python", "$(whoami)"],
            ["python", "`id`"],
            ["sh", "-c", "malicious"],
            ["bash", "-c", "evil"],
        ]
        for cmd in injection_attempts:
            result = validate_command(cmd)
            # Should either reject the command or flag dangerous patterns
            assert not result.valid or len(result.warnings) > 0, f"Expected {cmd} to be flagged"

    def test_blocked_commands(self):
        """Test that dangerous commands are blocked."""
        blocked = [
            ["rm", "-rf", "/"],
            ["sudo", "anything"],
            ["curl", "http://evil.com"],
            ["wget", "http://evil.com"],
        ]
        validator = InputValidator()
        for cmd in blocked:
            result = validator.validate_command(cmd)
            assert not result.valid, f"Expected {cmd} to be blocked"

    def test_valid_docker_image(self):
        """Test validation of valid Docker images."""
        valid_images = [
            "myimage:latest",
            "myimage:v1.0",
            "registry.example.com/myimage:tag",
            "ghcr.io/user/repo:sha256abc",
        ]
        for image in valid_images:
            result = validate_docker_image(image)
            assert result.valid, f"Expected '{image}' to be valid"

    def test_invalid_docker_image(self):
        """Test validation rejects invalid Docker images."""
        invalid_images = [
            "",
            None,
            "image;rm -rf /",
            "image`whoami`",
            "image$(id)",
        ]
        for image in invalid_images:
            result = validate_docker_image(image)
            assert not result.valid, f"Expected '{image}' to be invalid"

    def test_valid_environment_variables(self):
        """Test validation of valid environment variables."""
        valid_env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "MY_VAR_123": "value",
            "_PRIVATE": "secret",
        }
        result = validate_environment_variables(valid_env)
        assert result.valid

    def test_invalid_environment_variables(self):
        """Test validation rejects invalid environment variables."""
        # Invalid key format
        result = validate_environment_variables({"123BAD": "value"})
        assert not result.valid

        # Too many variables
        large_env = {f"VAR_{i}": "value" for i in range(200)}
        result = validate_environment_variables(large_env)
        assert not result.valid


class TestSanitizer:
    """Tests for input sanitization."""

    def test_sanitize_command_argument(self):
        """Test sanitization of command arguments."""
        sanitizer = Sanitizer()

        # Normal argument passes through
        assert sanitizer.sanitize_command_argument("normal") == "normal"

        # Shell metacharacters are removed/replaced
        dangerous = "arg;rm -rf /"
        sanitized = sanitizer.sanitize_command_argument(dangerous)
        assert ";" not in sanitized  # Semicolon should be removed/replaced

    def test_sanitize_command_removes_injection(self):
        """Test that injection characters are sanitized."""
        dangerous_inputs = [
            ("test;whoami", ";"),
            ("test|cat", "|"),
            ("test`id`", "`"),
            ("test$(cmd)", "$"),
            ("test&&evil", "&"),
            ("test||fallback", "|"),
            ("test>file", ">"),
            ("test<input", "<"),
        ]
        for dangerous, char in dangerous_inputs:
            result = sanitize_command_argument(dangerous)
            assert char not in result, f"Character '{char}' should be removed"

    def test_sanitize_environment_value(self):
        """Test sanitization of environment values."""
        # Normal value passes through
        assert sanitize_environment_value("normal_value") == "normal_value"

        # Null bytes are removed
        assert "\x00" not in sanitize_environment_value("value\x00with\x00nulls")

        # Newlines are handled
        result = sanitize_environment_value("line1\nline2", allow_newlines=False)
        assert "\n" not in result

    def test_sanitize_log_message(self):
        """Test sanitization of log messages."""
        # Normal message passes through
        assert "test message" in sanitize_log_message("test message")

        # Control characters are escaped
        result = sanitize_log_message("line1\nline2\rline3")
        assert "\n" not in result
        assert "\r" not in result
        assert "\\n" in result
        assert "\\r" in result

        # Long messages are truncated
        long_msg = "x" * 20000
        result = sanitize_log_message(long_msg)
        assert len(result) < 15000
        assert "truncated" in result

    def test_sanitize_path_prevents_traversal(self):
        """Test that path traversal is prevented."""
        sanitizer = Sanitizer()

        # Normal paths work
        assert sanitizer.sanitize_path("normal/path") == "normal/path"

        # Path traversal raises error
        with pytest.raises(ValueError, match="traversal"):
            sanitizer.sanitize_path("../etc/passwd")

        with pytest.raises(ValueError, match="traversal"):
            sanitizer.sanitize_path("foo/../../bar")

    def test_sanitize_path_rejects_absolute(self):
        """Test that absolute paths can be rejected."""
        sanitizer = Sanitizer()

        with pytest.raises(ValueError, match="[Aa]bsolute"):
            sanitizer.sanitize_path("/etc/passwd", allow_absolute=False)

    def test_sanitize_for_json(self):
        """Test JSON sanitization."""
        sanitizer = Sanitizer()

        data = {
            "normal": "value",
            "with_null": "value\x00null",
            "nested": {"key": "value\x01bad"},
        }

        result = sanitizer.sanitize_for_json(data)
        assert "\x00" not in str(result)
        assert "\x01" not in str(result)


class TestRateLimiter:
    """Tests for rate limiting."""

    def test_token_bucket_basic(self):
        """Test basic token bucket functionality."""
        bucket = TokenBucket(rate=10.0, capacity=5)

        # Should allow up to capacity requests immediately
        for _ in range(5):
            allowed, wait = bucket.consume()
            assert allowed
            assert wait == 0.0

        # Next request should be denied
        allowed, wait = bucket.consume()
        assert not allowed
        assert wait > 0

    def test_token_bucket_refill(self):
        """Test token bucket refills over time."""
        bucket = TokenBucket(rate=100.0, capacity=10)  # Fast rate for testing

        # Consume all tokens
        for _ in range(10):
            bucket.consume()

        # Wait for refill
        time.sleep(0.1)  # Should add ~10 tokens

        # Should allow requests again
        allowed, _ = bucket.consume()
        assert allowed

    def test_rate_limiter_allows_within_limit(self):
        """Test rate limiter allows requests within limit."""
        config = RateLimitConfig(requests_per_second=100, burst_size=10)
        limiter = InMemoryRateLimiter(config)

        # Should allow burst_size requests
        for i in range(10):
            result = limiter.consume("test_key")
            assert result.allowed, f"Request {i} should be allowed"

    def test_rate_limiter_blocks_excess(self):
        """Test rate limiter blocks excess requests."""
        config = RateLimitConfig(requests_per_second=1, burst_size=2)
        limiter = InMemoryRateLimiter(config)

        # Consume burst
        limiter.consume("test_key")
        limiter.consume("test_key")

        # Third request should be blocked
        result = limiter.consume("test_key")
        assert not result.allowed
        assert result.retry_after is not None
        assert result.retry_after > 0

    def test_rate_limiter_per_key_isolation(self):
        """Test rate limiter tracks keys independently."""
        config = RateLimitConfig(requests_per_second=1, burst_size=1)
        limiter = InMemoryRateLimiter(config)

        # Use up limit for key1
        limiter.consume("key1")
        result1 = limiter.consume("key1")
        assert not result1.allowed

        # key2 should still have capacity
        result2 = limiter.consume("key2")
        assert result2.allowed

    def test_rate_limit_result_headers(self):
        """Test rate limit result generates proper headers."""
        result = RateLimitResult(
            allowed=False,
            remaining=0,
            reset_at=time.time() + 60,
            retry_after=30.0,
            limit=100,
        )

        headers = result.to_headers()
        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Remaining" in headers
        assert "X-RateLimit-Reset" in headers
        assert "Retry-After" in headers


class TestSecretsManagement:
    """Tests for secrets management."""

    def test_is_sensitive_key(self):
        """Test detection of sensitive key names."""
        sensitive_keys = [
            "PASSWORD",
            "API_KEY",
            "SECRET",
            "AUTH_TOKEN",
            "aws_secret_access_key",
            "github_token",
            "DATABASE_PASSWORD",
            "my_password_field",
            "api_key_prod",
        ]
        for key in sensitive_keys:
            assert is_sensitive_key(key), f"'{key}' should be detected as sensitive"

    def test_is_not_sensitive_key(self):
        """Test that normal keys are not flagged."""
        normal_keys = [
            "PATH",
            "HOME",
            "USER",
            "HOSTNAME",
            "my_config",
            "debug_mode",
        ]
        for key in normal_keys:
            assert not is_sensitive_key(key), f"'{key}' should not be flagged as sensitive"

    def test_mask_sensitive_value(self):
        """Test masking of sensitive values."""
        # Short values are fully masked
        result = mask_sensitive_value("abc")
        assert "abc" not in result
        assert "*" in result

        # Longer values show prefix
        result = mask_sensitive_value("secretpassword123")
        assert result.startswith("secr")
        assert "password" not in result
        assert "*" in result

    def test_secrets_mask_dict(self):
        """Test masking of dictionary values."""
        mask = SecretsMask()
        data = {
            "username": "john",
            "password": "secret123",
            "api_key": "key-abc-def-ghi",
            "config": "normal_value",
        }

        masked = mask.mask_dict(data)
        assert masked["username"] == "john"  # Not sensitive
        assert masked["config"] == "normal_value"  # Not sensitive
        assert "secret" not in masked["password"]  # Masked
        assert "abc" not in masked["api_key"]  # Masked
        assert "*" in masked["password"]
        assert "*" in masked["api_key"]

    def test_secure_environment_get(self):
        """Test secure environment variable access."""
        env = SecureEnvironment({"MY_VAR": "value", "MY_SECRET": "secret123"})

        # Normal access
        assert env.get("MY_VAR") == "value"

        # Masked access for sensitive keys
        masked = env.get_masked("MY_SECRET")
        assert "secret123" not in masked
        assert "*" in masked

    def test_secure_environment_required(self):
        """Test required environment variable handling."""
        env = SecureEnvironment({"EXISTS": "value"})

        assert env.get("EXISTS", required=True) == "value"

        with pytest.raises(KeyError):
            env.get("MISSING", required=True)

    def test_secure_environment_to_dict_masks(self):
        """Test that to_dict masks sensitive values."""
        env = SecureEnvironment(
            {
                "PATH": "/usr/bin",
                "PASSWORD": "secret123",
            }
        )

        result = env.to_dict(mask_sensitive=True)
        assert result["PATH"] == "/usr/bin"
        assert "secret" not in result["PASSWORD"]

    def test_redact_secrets_in_string(self):
        """Test redaction of secrets in strings."""
        # Test password redaction
        text = "password=mysecret in the string"
        result = redact_secrets_in_string(text)
        assert "mysecret" not in result
        assert "[REDACTED]" in result

        # Test bearer token redaction
        text2 = "Authorization: Bearer eyJtoken123"
        result2 = redact_secrets_in_string(text2)
        assert "eyJtoken123" not in result2

    def test_create_secure_env_filters_sensitive(self):
        """Test that secure env creation filters sensitive vars."""
        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "AWS_SECRET_ACCESS_KEY": "secret",
                "MY_VAR": "value",
            },
        ):
            env = create_secure_env_for_provider(sensitive_key_filter=True)
            env_dict = env.to_dict(mask_sensitive=False)

            assert "PATH" in env_dict
            assert "MY_VAR" in env_dict
            # AWS secret should be filtered out
            assert "AWS_SECRET_ACCESS_KEY" not in env_dict


class TestSecurityEventHandler:
    """Tests for security event handling."""

    def test_security_event_creation(self):
        """Test creation of security events."""
        event = SecurityEvent(
            event_type=SecurityEventType.VALIDATION_FAILED,
            severity=SecuritySeverity.MEDIUM,
            message="Test validation failure",
            provider_id="test_provider",
        )

        assert event.event_id  # Should be generated
        assert event.event_type == SecurityEventType.VALIDATION_FAILED
        assert event.severity == SecuritySeverity.MEDIUM
        assert event.provider_id == "test_provider"

    def test_security_event_to_dict(self):
        """Test security event serialization."""
        event = SecurityEvent(
            event_type=SecurityEventType.INJECTION_ATTEMPT,
            severity=SecuritySeverity.HIGH,
            message="Injection detected",
            details={"field": "command", "pattern": ";"},
        )

        data = event.to_dict()
        assert data["event_type"] == "injection_attempt"
        assert data["severity"] == "high"
        assert "field" in data["details"]

    def test_in_memory_security_sink(self):
        """Test in-memory security event storage."""
        sink = InMemorySecuritySink(max_events=100)

        event = SecurityEvent(
            event_type=SecurityEventType.RATE_LIMIT_EXCEEDED,
            severity=SecuritySeverity.MEDIUM,
            message="Rate limit exceeded",
        )

        sink.emit(event)
        assert sink.count == 1

        # Query should find it
        results = sink.query(event_type=SecurityEventType.RATE_LIMIT_EXCEEDED)
        assert len(results) == 1
        assert results[0].message == "Rate limit exceeded"

    def test_security_handler_logs_validation_failure(self):
        """Test security handler logs validation failures."""
        sink = InMemorySecuritySink()
        handler = SecurityEventHandler(sink=sink)

        handler.log_validation_failed(
            field="command",
            message="Dangerous pattern detected",
            provider_id="test_provider",
        )

        events = sink.query(event_type=SecurityEventType.VALIDATION_FAILED)
        assert len(events) == 1
        assert events[0].provider_id == "test_provider"

    def test_security_handler_logs_injection_attempt(self):
        """Test security handler logs injection attempts."""
        sink = InMemorySecuritySink()
        handler = SecurityEventHandler(sink=sink)

        handler.log_injection_attempt(
            field="arguments",
            pattern=";",
            provider_id="bad_provider",
            source_ip="10.0.0.1",
        )

        events = sink.query(event_type=SecurityEventType.INJECTION_ATTEMPT)
        assert len(events) == 1
        assert events[0].severity == SecuritySeverity.HIGH

    def test_security_handler_logs_rate_limit(self):
        """Test security handler logs rate limit violations."""
        sink = InMemorySecuritySink()
        handler = SecurityEventHandler(sink=sink)

        handler.log_rate_limit_exceeded(
            provider_id="test",
            limit=100,
            window_seconds=60,
        )

        events = sink.query(event_type=SecurityEventType.RATE_LIMIT_EXCEEDED)
        assert len(events) == 1

    def test_security_handler_logs_suspicious_command(self):
        """Test security handler logs suspicious commands."""
        sink = InMemorySecuritySink()
        handler = SecurityEventHandler(sink=sink)

        handler.log_suspicious_command(
            command=["rm", "-rf", "/"],
            provider_id="evil_provider",
            reason="Destructive command blocked",
        )

        events = sink.query(event_type=SecurityEventType.SUSPICIOUS_COMMAND)
        assert len(events) == 1
        assert events[0].severity == SecuritySeverity.HIGH

    def test_security_event_severity_counts(self):
        """Test security event severity counting."""
        sink = InMemorySecuritySink()

        # Add events of different severities
        for severity in [
            SecuritySeverity.LOW,
            SecuritySeverity.LOW,
            SecuritySeverity.MEDIUM,
            SecuritySeverity.HIGH,
        ]:
            sink.emit(
                SecurityEvent(
                    event_type=SecurityEventType.ACCESS_DENIED,
                    severity=severity,
                    message="Test",
                )
            )

        counts = sink.get_severity_counts()
        assert counts["low"] == 2
        assert counts["medium"] == 1
        assert counts["high"] == 1


class TestSecureProviderLauncher:
    """Tests for secure provider launcher."""

    def test_launcher_blocks_dangerous_commands(self):
        """Test that dangerous commands are blocked."""
        from mcp_hangar.domain.services.provider_launcher import SubprocessLauncher

        launcher = SubprocessLauncher()

        dangerous_commands = [
            ["rm", "-rf", "/"],
            ["sudo", "anything"],
            ["bash", "-c", "evil"],
        ]

        for cmd in dangerous_commands:
            with pytest.raises(ValidationError):
                launcher._validate_command(cmd)

    def test_launcher_allows_python(self):
        """Test that Python commands are allowed."""
        from mcp_hangar.domain.services.provider_launcher import SubprocessLauncher

        launcher = SubprocessLauncher()

        # Python should always be allowed
        launcher._validate_command(["python", "-m", "mymodule"])
        launcher._validate_command(["python3", "script.py"])

    def test_launcher_filters_sensitive_env(self):
        """Test that sensitive env vars are filtered."""
        from mcp_hangar.domain.services.provider_launcher import SubprocessLauncher

        launcher = SubprocessLauncher(
            inherit_env=True,
            filter_sensitive_env=True,
        )

        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "AWS_SECRET_ACCESS_KEY": "secret",
                "NORMAL_VAR": "value",
            },
        ):
            env = launcher._prepare_env(None)

            assert "PATH" in env
            assert "NORMAL_VAR" in env
            # Sensitive vars should be filtered
            assert "AWS_SECRET_ACCESS_KEY" not in env

    def test_docker_launcher_validates_image(self):
        """Test Docker launcher validates image names."""
        from mcp_hangar.domain.services.provider_launcher import DockerLauncher

        launcher = DockerLauncher()

        # Valid images should pass
        launcher._validate_image("myregistry/myimage:tag")

        # Injection attempts should fail
        with pytest.raises(ValidationError):
            launcher._validate_image("image;rm -rf /")

    def test_docker_launcher_security_flags(self):
        """Test Docker launcher adds security flags."""
        from mcp_hangar.domain.services.provider_launcher import DockerLauncher

        launcher = DockerLauncher(
            enable_network=False,
            memory_limit="256m",
            read_only=True,
            drop_capabilities=True,
        )

        cmd = launcher._build_docker_command("myimage:latest", {})

        assert "--network" in cmd
        assert "none" in cmd
        assert "--memory" in cmd
        assert "256m" in cmd
        assert "--read-only" in cmd
        assert "--cap-drop" in cmd
        assert "ALL" in cmd

    @pytest.mark.parametrize(
        "network_mode,expected_flag",
        [
            ("none", "none"),
            ("bridge", "bridge"),
            ("host", "host"),
        ],
    )
    def test_container_launcher_network_modes(self, network_mode, expected_flag):
        """Test ContainerLauncher correctly sets network modes."""
        from unittest.mock import patch

        from mcp_hangar.domain.services.provider_launcher import ContainerConfig, ContainerLauncher

        with patch.object(ContainerLauncher, "_detect_runtime", return_value="docker"):
            launcher = ContainerLauncher(runtime="docker")

        config = ContainerConfig(
            image="test:latest",
            network=network_mode,
        )

        cmd = launcher._build_command(config)

        assert "--network" in cmd
        network_idx = cmd.index("--network")
        assert cmd[network_idx + 1] == expected_flag


class TestDangerousPatterns:
    """Test that dangerous patterns are properly detected."""

    @pytest.mark.parametrize(
        "pattern,test_string",
        [
            ("; ", "cmd; evil"),
            ("| ", "cmd | evil"),
            ("`", "cmd `whoami`"),
            ("$(", "cmd $(id)"),
            ("${", "cmd ${PATH}"),
            ("&&", "cmd && evil"),
            ("||", "cmd || fallback"),
            ("> ", "cmd > file"),
            ("< ", "cmd < file"),
            ("\n", "cmd\nevil"),
            ("\r", "cmd\revil"),
            ("\x00", "cmd\x00evil"),
        ],
    )
    def test_dangerous_pattern_detection(self, pattern, test_string):
        """Test each dangerous pattern is detected."""
        detected = False
        for regex in DANGEROUS_PATTERNS:
            if regex.search(test_string):
                detected = True
                break
        assert detected, f"Pattern '{pattern}' should be detected in '{test_string}'"


class TestIntegrationSecurity:
    """Integration tests for security features."""

    def test_full_validation_flow(self):
        """Test complete validation flow for a request."""
        validator = InputValidator()

        # Simulate validating a tool invocation request
        result = validator.validate_all(
            provider_id="my_provider",
            tool_name="my_tool",
            arguments={"param": "value"},
            timeout=30.0,
        )

        assert result.valid
        assert len(result.errors) == 0

    def test_full_validation_flow_with_injection(self):
        """Test validation flow catches injection attempts."""
        validator = InputValidator()

        # Simulate attack attempt
        result = validator.validate_all(
            provider_id="provider; rm -rf /",
            tool_name="../../../etc/passwd",
            arguments={"cmd": "$(whoami)"},
            timeout=30.0,
        )

        assert not result.valid
        assert len(result.errors) > 0

    def test_end_to_end_secure_launch_simulation(self):
        """Test end-to-end secure launch simulation (without actually launching)."""
        from mcp_hangar.domain.services.provider_launcher import SubprocessLauncher

        launcher = SubprocessLauncher()

        # Validate a safe command
        command = ["python", "-m", "my_provider"]
        env = {"MY_CONFIG": "value"}

        launcher._validate_command(command)
        launcher._validate_env(env)
        prepared_env = launcher._prepare_env(env)

        assert "MY_CONFIG" in prepared_env
        assert prepared_env["MY_CONFIG"] == "value"
