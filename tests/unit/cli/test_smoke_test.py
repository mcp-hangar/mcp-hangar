"""Tests for smoke_test module."""

from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import yaml

from mcp_hangar.server.cli.services.smoke_test import (
    _get_suggestion_for_error,
    _test_single_provider,
    ProviderTestResult,
    run_smoke_test,
    SmokeTestResult,
)


class TestProviderTestResult:
    """Tests for ProviderTestResult dataclass."""

    def test_success_result(self):
        """Should create success result."""
        result = ProviderTestResult(
            provider_id="test",
            success=True,
            state="ready",
            duration_ms=100.0,
        )
        assert result.success is True
        assert result.error is None

    def test_failure_result(self):
        """Should create failure result with error."""
        result = ProviderTestResult(
            provider_id="test",
            success=False,
            state="dead",
            duration_ms=50.0,
            error="Connection refused",
            suggestion="Check endpoint",
        )
        assert result.success is False
        assert result.error == "Connection refused"
        assert result.suggestion == "Check endpoint"


class TestSmokeTestResult:
    """Tests for SmokeTestResult dataclass."""

    def test_all_passed_true(self):
        """Should return True when all providers passed."""
        results = [
            ProviderTestResult("p1", True, "ready", 100.0),
            ProviderTestResult("p2", True, "ready", 200.0),
        ]
        smoke_result = SmokeTestResult(results=results, total_duration_ms=300.0)
        assert smoke_result.all_passed is True
        assert smoke_result.passed_count == 2
        assert smoke_result.failed_count == 0

    def test_all_passed_false(self):
        """Should return False when any provider failed."""
        results = [
            ProviderTestResult("p1", True, "ready", 100.0),
            ProviderTestResult("p2", False, "dead", 50.0, error="Failed"),
        ]
        smoke_result = SmokeTestResult(results=results, total_duration_ms=150.0)
        assert smoke_result.all_passed is False
        assert smoke_result.passed_count == 1
        assert smoke_result.failed_count == 1

    def test_empty_results(self):
        """Should handle empty results."""
        smoke_result = SmokeTestResult(results=[], total_duration_ms=0)
        assert smoke_result.all_passed is True
        assert smoke_result.passed_count == 0
        assert smoke_result.failed_count == 0


class TestGetSuggestionForError:
    """Tests for _get_suggestion_for_error function."""

    def test_command_not_found(self):
        """Should suggest checking PATH for command not found."""
        suggestion = _get_suggestion_for_error("Command not found: npx")
        assert suggestion is not None
        assert "PATH" in suggestion

    def test_permission_denied(self):
        """Should suggest checking permissions."""
        suggestion = _get_suggestion_for_error("Permission denied")
        assert suggestion is not None
        assert "permission" in suggestion.lower()

    def test_connection_refused(self):
        """Should suggest checking endpoint."""
        suggestion = _get_suggestion_for_error("Connection refused")
        assert suggestion is not None
        assert "endpoint" in suggestion.lower()

    def test_image_not_found(self):
        """Should suggest checking Docker image."""
        suggestion = _get_suggestion_for_error("Image not found: myimage:latest")
        assert suggestion is not None
        assert "image" in suggestion.lower()

    def test_module_not_found(self):
        """Should suggest installing dependencies."""
        suggestion = _get_suggestion_for_error("ModuleNotFoundError: No module named 'foo'")
        assert suggestion is not None
        assert "dependencies" in suggestion.lower()

    def test_unknown_error(self):
        """Should return None for unknown errors."""
        suggestion = _get_suggestion_for_error("Some random error xyz")
        assert suggestion is None


class TestTestSingleProvider:
    """Tests for _test_single_provider function."""

    @patch("mcp_hangar.server.cli.services.smoke_test.Provider")
    def test_successful_start(self, mock_provider_class):
        """Should return success when provider starts successfully."""
        mock_provider = MagicMock()
        mock_provider.state = MagicMock()
        mock_provider.state.__eq__ = lambda self, other: True  # Always READY
        mock_provider_class.return_value = mock_provider

        # Patch ProviderState.READY
        with patch("mcp_hangar.server.cli.services.smoke_test.ProviderState") as mock_state:
            mock_state.READY = "ready"
            mock_provider.state = mock_state.READY

            result = _test_single_provider(
                provider_id="test",
                provider_config={"mode": "subprocess", "command": ["echo", "hi"]},
                timeout_s=5.0,
            )

        assert result.success is True
        assert result.state == "ready"

    @patch("mcp_hangar.server.cli.services.smoke_test.Provider")
    def test_start_failure(self, mock_provider_class):
        """Should return failure when provider fails to start."""
        from mcp_hangar.domain.exceptions import ProviderStartError

        mock_provider_class.side_effect = ProviderStartError(
            provider_id="test",
            reason="Failed to connect",
        )

        result = _test_single_provider(
            provider_id="test",
            provider_config={"mode": "subprocess", "command": ["invalid"]},
            timeout_s=5.0,
        )

        assert result.success is False
        assert result.state == "dead"
        assert "Failed to connect" in result.error


class TestRunSmokeTest:
    """Tests for run_smoke_test function."""

    def test_empty_config(self):
        """Should handle config with no providers."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"providers": {}}, f)
            config_path = Path(f.name)

        try:
            result = run_smoke_test(config_path, timeout_s=5.0)
            assert result.all_passed is True
            assert len(result.results) == 0
        finally:
            config_path.unlink()

    def test_missing_providers_section(self):
        """Should handle config with empty providers section."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            # Note: load_configuration requires 'providers' section
            yaml.dump({"providers": {}}, f)
            config_path = Path(f.name)

        try:
            result = run_smoke_test(config_path, timeout_s=5.0)
            assert result.all_passed is True
            assert len(result.results) == 0
        finally:
            config_path.unlink()
