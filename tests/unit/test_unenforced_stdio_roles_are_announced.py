"""Declared stdio roles that bound nothing are announced at boot (#2079).

With auth off in `egress` mode every management tool is allowed before the
declared principal is consulted, so `roles: [viewer]` had no effect and nothing
said so.
"""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from mcp_hangar.auth.stdio_principal import clear_stdio_principal
from mcp_hangar.server.bootstrap import _declare_stdio_principal

PRINCIPAL = {"id": "local-user", "tenant_id": "local", "roles": ["viewer"]}


@pytest.fixture(autouse=True)
def _clear():
    yield
    clear_stdio_principal()


def _warnings(config: dict[str, Any], *, stdio: bool = True) -> list[dict[str, Any]]:
    with capture_logs() as logs:
        _declare_stdio_principal(config, stdio=stdio)
    return [entry for entry in logs if entry["event"] == "stdio_principal_roles_not_enforced"]


def test_auth_off_in_egress_is_announced_once_naming_the_roles() -> None:
    (warning,) = _warnings({"auth": {"stdio": {"principal": PRINCIPAL}}})

    assert warning["log_level"] == "warning"
    assert warning["principal_id"] == "local-user" and warning["roles"] == ["viewer"]
    assert "front_door" in warning["hint"]


@pytest.mark.parametrize(
    "config",
    [
        {"auth": {"stdio": {"principal": PRINCIPAL}}, "tool_access": {"mode": "front_door"}},
        {"auth": {"enabled": True, "stdio": {"principal": PRINCIPAL}}},
        {"auth": {}},
    ],
)
def test_nothing_is_announced_where_the_roles_do_bind_or_none_are_declared(config) -> None:
    assert _warnings(config) == []


def test_nothing_is_announced_over_http() -> None:
    assert _warnings({"auth": {"stdio": {"principal": PRINCIPAL}}}, stdio=False) == []
