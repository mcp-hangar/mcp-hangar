"""One actor reader and one fallback across the REST routes (#1720).

`server/api/config.py` recorded `"api"` with auth off while the auth routes
recorded `"anonymous"`.
"""

from types import SimpleNamespace

from starlette.datastructures import State

from mcp_hangar.auth.api.routes import _actor
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.api.actor import request_actor
from mcp_hangar.server.api.config import _requested_by


def _request(principal_id: str | None) -> SimpleNamespace:
    scope: dict = {}
    if principal_id is not None:
        principal = Principal(id=PrincipalId(principal_id), type=PrincipalType.USER)
        scope = {"state": State({"auth": SimpleNamespace(principal=principal)})}
    return SimpleNamespace(scope=scope)


def test_with_auth_off_every_route_records_anonymous():
    request = _request(None)
    assert _requested_by(request) == _actor(request) == request_actor(request) == "anonymous"


def test_with_auth_on_every_route_records_the_caller():
    request = _request("user:admin")
    assert _requested_by(request) == _actor(request) == request_actor(request) == "user:admin"


def test_the_router_dict_state_shape_is_read_too():
    request = SimpleNamespace(scope={"state": {"auth": _request("user:admin").scope["state"].auth}})
    assert request_actor(request) == "user:admin"
