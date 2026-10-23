"""Tests for the secrets resolver."""

import os
import tempfile
from pathlib import Path

import pytest

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
            result = resolver.resolve(["TEST_SECRET_123"], "test-provider", allowed=["TEST_SECRET_123"])
            assert result.all_resolved is True
            assert result.resolved["TEST_SECRET_123"] == "test_value"
            assert result.sources["TEST_SECRET_123"] == "env"
        finally:
            del os.environ["TEST_SECRET_123"]

    def test_resolve_missing_secret(self):
        """Test resolving missing secret."""
        resolver = SecretsResolver()
        result = resolver.resolve(["NONEXISTENT_SECRET_XYZ"], "test-provider", allowed=["NONEXISTENT_SECRET_XYZ"])
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
            result = resolver.resolve(["MY_SECRET"], "test-provider", allowed=["MY_SECRET"])

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
            result = resolver.resolve(["GLOBAL_SECRET"], "other-provider", allowed=["GLOBAL_SECRET"])

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
                result = resolver.resolve(["PRECEDENCE_SECRET"], "test-provider", allowed=["PRECEDENCE_SECRET"])

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
            result = resolver.resolve(["SHARED_SECRET"], "test-provider", allowed=["SHARED_SECRET"])

            assert result.resolved["SHARED_SECRET"] == "provider_value"

    def test_get_missing_instructions(self):
        """Test generating missing secrets instructions."""
        resolver = SecretsResolver()
        instructions = resolver.get_missing_instructions(
            SecretsResult(missing=["API_KEY", "SECRET_TOKEN"]),
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
                    allowed={"ENV_SECRET_MULTI", "FILE_SECRET", "MISSING_SECRET"},
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
            result = resolver.resolve(["WHITESPACE_SECRET"], "test-provider", allowed=["WHITESPACE_SECRET"])

            assert result.resolved["WHITESPACE_SECRET"] == "secret_with_whitespace"


class TestANameThatIsNotAVariableNameIsRefused:
    """The names come from a registry entry; one that could not be an environment
    variable is refused before the environment or a file is consulted."""

    def test_a_trailing_newline_is_not_accepted(self, tmp_path):
        """`ENV_KEY_PATTERN` ends in `$`, which `match` lets through before a final newline."""
        (tmp_path / "KEY\n").write_text("value")

        result = SecretsResolver(secrets_dir=tmp_path).resolve(["KEY\n"], "test-provider", allowed=["KEY\n"])

        assert result.invalid == ["KEY\n"]
        assert result.resolved == {}

    def test_a_path_is_not_a_name(self, tmp_path):
        result = SecretsResolver(secrets_dir=tmp_path).resolve(
            ["a/b", "..", "OK_NAME"], "test-provider", allowed=["a/b", "..", "OK_NAME"]
        )

        assert result.invalid == ["a/b", ".."]
        assert result.missing == ["a/b", "..", "OK_NAME"]


class TestANameTheServerIsNotBoundToIsNotLookedUp:
    def test_it_is_missing_even_when_it_is_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UNBOUND_SECRET", "host-value")
        (tmp_path / "UNBOUND_SECRET").write_text("file-value")

        result = SecretsResolver(secrets_dir=tmp_path).resolve(["UNBOUND_SECRET"], "test-provider", allowed=())

        assert result.resolved == {}
        assert result.not_allowed == ["UNBOUND_SECRET"]
        assert result.missing == ["UNBOUND_SECRET"]

    def test_the_instructions_name_the_key_the_server_and_the_variable(self, tmp_path):
        resolver = SecretsResolver(secrets_dir=tmp_path)
        result = resolver.resolve(["WEATHER_REGION"], "io.github.acme/weather", allowed=())

        instructions = resolver.get_missing_instructions(result, "io.github.acme/weather")

        assert "hot_loading.secrets.allow" in instructions
        assert "Ask the operator to allow 'WEATHER_REGION' for 'io.github.acme/weather'" in instructions
        assert "allow:" not in instructions
        assert "export WEATHER_REGION" not in instructions, "setting it would change nothing until it is bound"


class TestSecretsDirSegments:
    """The per-server directory is the registry name, one directory per `/` segment."""

    def test_a_registry_name_is_its_segments(self):
        from mcp_hangar.application.services.registry_ids import secrets_dir_segments

        assert secrets_dir_segments("io.github.acme/weather") == ("io.github.acme", "weather")
        assert secrets_dir_segments("io.github.acme/tools/x-y_z") == ("io.github.acme", "tools", "x-y_z")

    @pytest.mark.parametrize(
        "name",
        [
            "/io.github.acme/weather",  # leading `/`: an empty first segment
            "io.github.acme/weather/",  # trailing `/`
            "io.github.acme//weather",  # `//`
            "io.github.acme/./weather",
            "io.github.acme/../weather",
            "..",
            "io.github.acme/weather.",  # Windows strips it: one directory with `weather`
            "io.github.acme./weather",
            "io.github.acme/wea ther",
            "io.github.acme/wea\\ther",
            "",
        ],
    )
    def test_a_name_that_could_not_be_one_directory_per_segment_has_none(self, name):
        from mcp_hangar.application.services.registry_ids import secrets_dir_segments

        assert secrets_dir_segments(name) is None
