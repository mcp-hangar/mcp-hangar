"""`auth.opa.require_both` makes OPA able only to refuse more; the default is announced (#2081).

With OPA enabled, an OPA allow overrode an RBAC deny and its tenant scope, and
nothing in the configuration said so. `CombinedAuthorizer` already had the
narrowing mode; no setting reached it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.config import parse_auth_config
from mcp_hangar.domain.contracts.authorization import AuthorizationRequest, AuthorizationResult
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType


def _auth(require_both: object = None) -> dict:
    opa: dict = {"enabled": True, "url": "http://127.0.0.1:1"}
    if require_both is not None:
        opa["require_both"] = require_both
    return {"enabled": True, "api_key": {"enabled": True}, "opa": opa}


def _role_less_request() -> AuthorizationRequest:
    principal = Principal(id=PrincipalId("svc:nobody"), type=PrincipalType.SERVICE_ACCOUNT)
    return AuthorizationRequest(principal=principal, action="*", resource_type="admin", resource_id="*")


def _verdict(require_both: object) -> AuthorizationResult:
    components = bootstrap_auth(parse_auth_config(_auth(require_both)))
    with patch(
        "mcp_hangar.auth.infrastructure.opa_authorizer.OPAAuthorizer.authorize",
        return_value=AuthorizationResult.allow(reason="opa_allow"),
    ):
        return components.authz_middleware._authorizer.authorize(_role_less_request())


def test_by_default_an_opa_allow_still_overrides_an_rbac_deny() -> None:
    assert _verdict(None).allowed


def test_require_both_keeps_the_rbac_deny() -> None:
    assert not _verdict(True).allowed


def test_the_default_is_announced_and_require_both_is_not() -> None:
    with capture_logs() as default_logs:
        bootstrap_auth(parse_auth_config(_auth()))
    with capture_logs() as narrowing_logs:
        bootstrap_auth(parse_auth_config(_auth(True)))

    assert [e for e in default_logs if e["event"] == "opa_may_widen_rbac" and e["log_level"] == "warning"]
    assert not [e for e in narrowing_logs if e["event"] == "opa_may_widen_rbac"]


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_a_value_that_is_not_a_boolean_refuses_the_configuration(value) -> None:
    with pytest.raises(ConfigurationError, match="require_both"):
        parse_auth_config(_auth(value))
