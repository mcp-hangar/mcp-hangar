"""A backup that cannot be written says why, instead of saying nothing.

`POST /api/config/backup` writes beside the configuration file. In the
published image that directory belongs to root while the gateway runs as
`hangar`, so the write fails on **every** deployment that uses it -- and the
caller received:

    500 {"error": {"code": "InternalServerError",
                   "message": "An internal server error occurred."}}

with `PermissionError: [Errno 13] Permission denied: 'config.yaml.bak1'` visible
only in the log. That tells an operator the gateway is broken when the gateway
is working and the filesystem said no.

Found by running the REST reference against 2.5.0-rc.3.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp_hangar.domain.exceptions import (
    ConfigurationError,
    ConfigurationUnavailableError,
    ValidationError,
)
from mcp_hangar.server.api.middleware import _get_status_code


class _Request:
    """Just enough Request for the handler: a body it can fail to parse."""

    async def json(self):
        raise ValueError("no body")


async def _backup() -> object:
    from mcp_hangar.server.api.config import backup_config

    return await backup_config(_Request())


@pytest.mark.asyncio
class TestTheReasonReachesTheCaller:
    async def test_a_permission_error_names_the_directory(self) -> None:
        failure = PermissionError(13, "Permission denied")

        with patch("mcp_hangar.server.api.config.write_config_backup", side_effect=failure):
            # The narrow subclass -- not a bare ConfigurationError -- is what
            # earns the 503. Only this "the filesystem said no" case does.
            with pytest.raises(ConfigurationUnavailableError) as excinfo:
                await _backup()

        message = str(excinfo.value)
        assert "Permission denied" in message
        assert "writable" in message, "the caller needs to know what to fix"
        assert excinfo.value.details["errno"] == 13

    async def test_a_missing_directory_is_the_same_shape(self) -> None:
        with patch(
            "mcp_hangar.server.api.config.write_config_backup",
            side_effect=FileNotFoundError(2, "No such file or directory"),
        ):
            with pytest.raises(ConfigurationError) as excinfo:
                await _backup()

        assert "No such file or directory" in str(excinfo.value)

    async def test_a_successful_backup_still_returns_its_path(self) -> None:
        with patch("mcp_hangar.server.api.config.write_config_backup", return_value="/etc/x/config.yaml.bak1"):
            response = await _backup()

        assert b"/etc/x/config.yaml.bak1" in response.body

    async def test_an_unexpected_failure_is_not_dressed_up_as_configuration(self) -> None:
        # Only OSError means "the filesystem said no". A bug in serialization is
        # still a bug, and turning it into a 503 would tell the operator to check
        # their permissions about something that has nothing to do with them.
        with patch("mcp_hangar.server.api.config.write_config_backup", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                await _backup()


class TestTheStatusSaysWhoseProblemItIs:
    def test_the_unwritable_backup_is_503(self) -> None:
        # The narrow subclass -- the "the filesystem said no" case -- is the
        # only ConfigurationError that maps to 503.
        assert _get_status_code(ConfigurationUnavailableError("the directory is not writable")) == 503

    def test_a_generic_configuration_error_is_500_not_503(self) -> None:
        # #823 mapped EVERY ConfigurationError to 503, so an operator-input
        # problem (a bad capabilities block, the reload fault-barrier) came
        # back as a faked retryable outage carrying wrapped internal text.
        # A generic ConfigurationError must fall through to 500.
        assert _get_status_code(ConfigurationError("bad capabilities block in config.yaml")) == 500

    def test_the_subclass_wins_over_the_base_in_the_map(self) -> None:
        # Order is load-bearing: the subclass entry sits ahead of the base
        # MCPError->500 fallthrough, so isinstance matches 503 first for the
        # narrow case while the base still resolves to 500.
        assert _get_status_code(ConfigurationUnavailableError("x")) == 503
        assert _get_status_code(ConfigurationError("x")) == 500

    def test_validation_is_still_422(self) -> None:
        # ValidationError is not a ConfigurationError; the new entry must not
        # shadow it. Map order is load-bearing.
        assert _get_status_code(ValidationError("bad field")) == 422
