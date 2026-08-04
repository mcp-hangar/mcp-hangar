"""An incomplete request is the caller's problem, and must be answered as one.

Five mutating endpoints indexed the parsed body directly -- `body["group_id"]`,
`body["source_type"]`, `body["mcp_server_id"]` and so on. `KeyError` is not
`ValueError`, so it escaped each route's handler and became a 500 with "an
internal server error occurred".

Two costs, and the second is the one that lasts. The caller is told the server
broke when their request was merely incomplete, so they have nothing to act on.
And every such request lands in the log as an unhandled exception -- the same
signal a genuine fault produces, in exactly the channel that is supposed to stay
quiet.

A security audit found one of the five (SEC-04). The other four were the same
line of code in a different file, which is why the guard is shared and why this
file tests all of them: fixing only the reported one would have left four
identical 500s and a false sense that the class was closed.

Also here: with auth disabled, `/api/auth/keys` answered 500 (SEC-05). Its route
was mounted whenever the auth module merely imported, while its CQRS handlers
are registered only when auth is enabled -- so the route existed and its handler
did not.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

MUTATING_ENDPOINTS = [
    ("/mcp_servers/", {"mode": "subprocess"}, "mcp_server_id"),
    ("/mcp_servers/", {"mcp_server_id": "x"}, "mode"),
    ("/groups/", {}, "group_id"),
    ("/discovery/sources", {"mode": "subprocess"}, "source_type"),
]


@pytest.fixture(autouse=True)
def _leave_the_global_context_as_we_found_it():
    """Building the router populates the process-global ApplicationContext.

    `create_api_router` calls `attach_component_app_state`, which calls
    `get_context()`, which lazily CREATES one. A test that builds a router
    therefore leaves a real context behind for every test after it -- and
    `_check_permission` reads `auth_components` off exactly that object, so the
    next suite's authorization tests quietly start passing when they should
    fail. Two of them did, and it took a bisect to find that the cause was this
    file rather than the code under test.
    """
    from mcp_hangar.server.context import reset_context

    reset_context()
    yield
    reset_context()


@pytest.fixture
def ctx():
    context = Mock()
    context.command_bus = Mock()
    context.query_bus = Mock()
    return context


@pytest.fixture
def client(ctx, monkeypatch):
    """Just the routes under test, not the whole API router.

    `create_api_router` has process-global effects -- it populates the lazily
    created ApplicationContext, and it wraps module-level route lists that every
    other router build shares. Using it here turned two admin-tools
    authorization tests green in the same session, which took a bisect to trace
    back to this file rather than to the code under test. Body validation does
    not need any of that: mount the routes directly.
    """
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from mcp_hangar.server.api.discovery import discovery_routes
    from mcp_hangar.server.api.groups import group_routes
    from mcp_hangar.server.api.mcp_servers import mcp_server_routes

    for module in ("groups", "discovery", "mcp_servers"):
        monkeypatch.setattr(f"mcp_hangar.server.api.{module}.get_context", lambda: ctx)
    # Authorization is not the subject here -- it has its own suite, and
    # /mcp_servers/ correctly answers 401 before the body is ever parsed, which
    # is worth stating: this class of 500 needs `mcp_servers:write` to reach.
    monkeypatch.setattr("mcp_hangar.server.api.mcp_servers._check_permission", lambda *a, **k: None)

    app = Starlette(
        routes=[
            Mount("/mcp_servers", routes=mcp_server_routes),
            Mount("/groups", routes=group_routes),
            Mount("/discovery", routes=discovery_routes),
        ]
    )
    return TestClient(app, raise_server_exceptions=False)


class TestAnIncompleteBodyIsAClientError:
    @pytest.mark.parametrize(
        ("path", "body", "missing"),
        MUTATING_ENDPOINTS,
        ids=[f"{p}-{m}" for p, _, m in MUTATING_ENDPOINTS],
    )
    def test_it_answers_400_and_names_the_field(self, client, path, body, missing):
        response = client.post(path, json=body)

        assert response.status_code != 500, (
            f"POST {path} without {missing!r} still answers 500; the caller is told "
            "the server broke rather than that their request is incomplete"
        )
        assert response.status_code == 400
        assert missing in response.json().get("detail", "")

    def test_a_non_object_body_is_also_a_client_error(self, client):
        """`body` is whatever JSON decoded to, and a list has no keys either."""
        response = client.post("/mcp_servers/", json=["not", "an", "object"])
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_body"


class TestTheGuardIsSharedRatherThanCopied:
    """One helper, so the sixth endpoint added does not repeat the mistake."""

    def test_every_direct_body_index_sits_behind_a_guard(self):
        import pathlib
        import re

        api = pathlib.Path("src/mcp_hangar/server/api")
        gaps = []
        for path in sorted(api.glob("*.py")):
            if path.name == "request_body.py":  # its docstring quotes the pattern
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                for key in re.findall(r'body\["([^"]+)"\]', line):
                    guarded = False
                    for j in range(i, max(i - 40, -1), -1):
                        if "missing_fields(body" in lines[j] and f'"{key}"' in lines[j]:
                            guarded = True
                            break
                        if lines[j].lstrip().startswith(("async def ", "def ")):
                            break
                    if not guarded:
                        gaps.append(f"{path.name}:{i + 1} body[{key!r}]")
        assert gaps == [], (
            f"{len(gaps)} request-body index(es) with no preceding validation; "
            f"each is a KeyError away from a 500: {gaps}"
        )


class TestAnInactiveModuleSaysSoInsteadOfBreaking:
    """A route whose handler was never registered is a 503, not a 500.

    The auth routes are mounted whenever the auth module imports, while their
    CQRS handlers are registered only when auth is enabled. With auth off, the
    route existed and its handler did not, and the bus raised a bare ValueError
    that became "an internal server error occurred".

    The first attempt at this fixed it by not mounting the routes when
    `auth_components.enabled` was false. That was wrong, and the existing tests
    said so: `enabled` already answers two different questions in this codebase
    -- "mount the authentication middleware" and "is auth configured" -- and
    `test_runtime_withdrawal` deliberately sets it False on the router stub for
    a deployment that HAS auth. Overloading it a third time would have unmounted
    the routes in a configuration that wants them.

    So the route table is left alone, and the condition is typed instead:
    `HandlerNotRegisteredError` maps to 503. That answers correctly for any
    module that is present but inactive, not just auth.
    """

    def test_the_bus_raises_a_typed_error(self):
        from mcp_hangar.application.ports.bus import HandlerNotRegisteredError
        from mcp_hangar.infrastructure.query_bus import QueryBus

        class Unregistered:
            pass

        with pytest.raises(HandlerNotRegisteredError):
            QueryBus().execute(Unregistered())

    def test_it_still_reads_as_a_value_error(self):
        """Subclassed so the `except ValueError` blocks that predate it still work."""
        from mcp_hangar.application.ports.bus import HandlerNotRegisteredError

        assert issubclass(HandlerNotRegisteredError, ValueError)

    def test_the_api_maps_it_to_503(self):
        from mcp_hangar.application.ports.bus import HandlerNotRegisteredError
        from mcp_hangar.server.api.middleware import _get_status_code

        assert _get_status_code(HandlerNotRegisteredError("No handler registered for X")) == 503

    def test_a_plain_value_error_is_still_a_500(self):
        """The mapping must not turn every ValueError into a service-unavailable."""
        from mcp_hangar.server.api.middleware import _get_status_code

        assert _get_status_code(ValueError("something else went wrong")) == 500
