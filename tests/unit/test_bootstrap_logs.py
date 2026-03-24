"""Integration tests for log buffer bootstrap wiring (Phase 22-02).

Verifies that init_log_buffers() correctly wires ProviderLogBuffer instances
with the LogStreamBroadcaster callback into Provider aggregates.
"""

from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.domain.value_objects.log import LogLine
from mcp_hangar.infrastructure.persistence.log_buffer import (
    ProviderLogBuffer,
    clear_log_buffer_registry,
    get_log_buffer,
)
from mcp_hangar.server.bootstrap.logs import init_log_buffers
from mcp_hangar.server.api.ws.logs import LogStreamBroadcaster


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear the log buffer registry around each test."""
    clear_log_buffer_registry()
    yield
    clear_log_buffer_registry()


def _make_mock_provider(provider_id: str) -> MagicMock:
    """Return a MagicMock that looks like a Provider aggregate."""
    provider = MagicMock()
    provider.provider_id = provider_id
    return provider


# ---------------------------------------------------------------------------
# init_log_buffers tests
# ---------------------------------------------------------------------------


class TestInitLogBuffers:
    """Tests for the init_log_buffers bootstrap helper."""

    def test_creates_buffer_for_each_provider(self):
        """init_log_buffers registers a buffer for every provider in the dict."""
        providers = {
            "math": _make_mock_provider("math"),
            "weather": _make_mock_provider("weather"),
        }
        init_log_buffers(providers)

        assert get_log_buffer("math") is not None
        assert get_log_buffer("weather") is not None

    def test_injects_buffer_into_provider(self):
        """init_log_buffers calls provider.set_log_buffer() for each provider."""
        math_provider = _make_mock_provider("math")
        providers = {"math": math_provider}

        init_log_buffers(providers)

        math_provider.set_log_buffer.assert_called_once()
        injected = math_provider.set_log_buffer.call_args[0][0]
        assert isinstance(injected, ProviderLogBuffer)

    def test_buffer_provider_id_matches_provider(self):
        """The created buffer has the correct provider_id."""
        providers = {"math": _make_mock_provider("math")}
        init_log_buffers(providers)

        buf = get_log_buffer("math")
        assert buf is not None
        assert buf.provider_id == "math"

    def test_buffer_in_registry_matches_injected_buffer(self):
        """The buffer in the registry is the same instance as the one injected."""
        math_provider = _make_mock_provider("math")
        providers = {"math": math_provider}

        init_log_buffers(providers)

        registry_buf = get_log_buffer("math")
        injected_buf = math_provider.set_log_buffer.call_args[0][0]
        assert registry_buf is injected_buf

    def test_handles_empty_providers_dict(self):
        """init_log_buffers does not raise on an empty providers dict."""
        init_log_buffers({})  # should not raise

    def test_on_append_wires_broadcaster_notify(self):
        """Appending to a wired buffer triggers the broadcaster notify callback."""
        received: list[LogLine] = []

        mock_broadcaster = MagicMock(spec=LogStreamBroadcaster)
        mock_broadcaster.notify.side_effect = received.append

        providers = {"math": _make_mock_provider("math")}

        with patch(
            "mcp_hangar.server.api.ws.logs.log_broadcaster",
            mock_broadcaster,
        ):
            init_log_buffers(providers)

        buf = get_log_buffer("math")
        assert buf is not None

        line = LogLine(provider_id="math", stream="stderr", content="hello")
        buf.append(line)

        assert len(received) == 1
        assert received[0] is line

    def test_idempotent_second_call_replaces_buffer(self):
        """Calling init_log_buffers twice creates a fresh buffer (idempotent)."""
        providers = {"math": _make_mock_provider("math")}

        init_log_buffers(providers)
        first_buf = get_log_buffer("math")

        # Reset mock call count
        providers["math"].set_log_buffer.reset_mock()

        init_log_buffers(providers)
        second_buf = get_log_buffer("math")

        assert first_buf is not second_buf  # new buffer created
        providers["math"].set_log_buffer.assert_called_once()

    def test_multiple_providers_each_get_own_buffer(self):
        """Each provider gets its own independent buffer instance."""
        providers = {
            "math": _make_mock_provider("math"),
            "search": _make_mock_provider("search"),
        }
        init_log_buffers(providers)

        math_buf = get_log_buffer("math")
        search_buf = get_log_buffer("search")

        assert math_buf is not search_buf
        assert math_buf is not None and search_buf is not None
        assert math_buf.provider_id == "math"
        assert search_buf.provider_id == "search"
