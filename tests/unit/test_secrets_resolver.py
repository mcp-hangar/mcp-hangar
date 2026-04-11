"""Tests for the secrets resolver."""

import os
from pathlib import Path
import tempfile

from mcp_hangar.application.services.secrets_resolver import SecretsResolver, SecretsResult


class TestSecretsResult:
    """Tests for SecretsResult."""

    def test_all_resolved_when_empty(self):
        """Test all_resolved is True when no secrets required."""
        result = SecretsResult()
        assert result.all_resolved is True

    def test_all_resolved_when_all_found(self):
        """Test all_resolved is True when all secrets found."""
        result = SecretsResult(
            resolved={"API_KEY": "secret123"},
            missing=[],
            sources={"API_KEY": "env"},
        )
        assert result.all_resolved is True

    def test_all_resolved_when_some_missing(self):
        """Test all_resolved is False when some secrets missing."""
        result = SecretsResult(
            resolved={"API_KEY": "secret123"},
            missing=["OTHER_KEY"],
            sources={"API_KEY": "env"},
        )
        assert result.all_resolved is False


class TestSecretsResolver:
    """Tests for SecretsResolver."""

    def test_resolve_from_env(self):
        """Test resolving secrets from environment variables."""
        resolver = SecretsResolver()
        os.environ["TEST_SECRET_123"] = "test_value"
        try:
            result = resolver.resolve(["TEST_SECRET_123"], "test-provider")
            assert result.all_resolved is True
            assert result.resolved["TEST_SECRET_123"] == "test_value"
            assert result.sources["TEST_SECRET_123"] == "env"
        finally:
            del os.environ["TEST_SECRET_123"]

    def test_resolve_missing_secret(self):
        """Test resolving missing secret."""
        resolver = SecretsResolver()
        result = resolver.resolve(["NONEXISTENT_SECRET_XYZ"], "test-provider")
        assert result.all_resolved is False
        assert "NONEXISTENT_SECRET_XYZ" in result.missing

    def test_resolve_from_provider_file(self):
        """Test resolving secrets from provider-specific file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = Path(tmpdir)
            provider_dir = secrets_dir / "test-provider"
            provider_dir.mkdir()
            secret_file = provider_dir / "MY_SECRET"
            secret_file.write_text("secret_from_file")

            resolver = SecretsResolver(secrets_dir=secrets_dir)
            result = resolver.resolve(["MY_SECRET"], "test-provider")

            assert result.all_resolved is True
            assert result.resolved["MY_SECRET"] == "secret_from_file"
            assert result.sources["MY_SECRET"] == "file"

    def test_resolve_from_global_file(self):
        """Test resolving secrets from global file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = Path(tmpdir)
            secret_file = secrets_dir / "GLOBAL_SECRET"
            secret_file.write_text("global_value")

            resolver = SecretsResolver(secrets_dir=secrets_dir)
            result = resolver.resolve(["GLOBAL_SECRET"], "other-provider")

            assert result.all_resolved is True
            assert result.resolved["GLOBAL_SECRET"] == "global_value"
            assert result.sources["GLOBAL_SECRET"] == "file"

    def test_env_takes_precedence_over_file(self):
        """Test environment variable takes precedence over file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = Path(tmpdir)
            secret_file = secrets_dir / "PRECEDENCE_SECRET"
            secret_file.write_text("file_value")

            os.environ["PRECEDENCE_SECRET"] = "env_value"
            try:
                resolver = SecretsResolver(secrets_dir=secrets_dir)
                result = resolver.resolve(["PRECEDENCE_SECRET"], "test-provider")

                assert result.resolved["PRECEDENCE_SECRET"] == "env_value"
                assert result.sources["PRECEDENCE_SECRET"] == "env"
            finally:
                del os.environ["PRECEDENCE_SECRET"]

    def test_provider_file_takes_precedence_over_global(self):
        """Test provider-specific file takes precedence over global."""
        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = Path(tmpdir)

            global_file = secrets_dir / "SHARED_SECRET"
            global_file.write_text("global_value")

            provider_dir = secrets_dir / "test-provider"
            provider_dir.mkdir()
            provider_file = provider_dir / "SHARED_SECRET"
            provider_file.write_text("provider_value")

            resolver = SecretsResolver(secrets_dir=secrets_dir)
            result = resolver.resolve(["SHARED_SECRET"], "test-provider")

            assert result.resolved["SHARED_SECRET"] == "provider_value"

    def test_get_missing_instructions(self):
        """Test generating missing secrets instructions."""
        resolver = SecretsResolver()
        instructions = resolver.get_missing_instructions(
            ["API_KEY", "SECRET_TOKEN"],
            "stripe",
        )

        assert "API_KEY" in instructions
        assert "SECRET_TOKEN" in instructions
        assert "stripe" in instructions
        assert "export API_KEY" in instructions
        assert "~/.config/mcp-hangar/secrets" in instructions

    def test_resolve_multiple_secrets(self):
        """Test resolving multiple secrets."""
        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = Path(tmpdir)
            secret_file = secrets_dir / "FILE_SECRET"
            secret_file.write_text("file_value")

            os.environ["ENV_SECRET_MULTI"] = "env_value"
            try:
                resolver = SecretsResolver(secrets_dir=secrets_dir)
                result = resolver.resolve(
                    ["ENV_SECRET_MULTI", "FILE_SECRET", "MISSING_SECRET"],
                    "test-provider",
                )

                assert result.resolved["ENV_SECRET_MULTI"] == "env_value"
                assert result.resolved["FILE_SECRET"] == "file_value"
                assert "MISSING_SECRET" in result.missing
                assert result.all_resolved is False
            finally:
                del os.environ["ENV_SECRET_MULTI"]

    def test_file_content_is_stripped(self):
        """Test file content is stripped of whitespace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = Path(tmpdir)
            secret_file = secrets_dir / "WHITESPACE_SECRET"
            secret_file.write_text("  secret_with_whitespace  \n")

            resolver = SecretsResolver(secrets_dir=secrets_dir)
            result = resolver.resolve(["WHITESPACE_SECRET"], "test-provider")

            assert result.resolved["WHITESPACE_SECRET"] == "secret_with_whitespace"
