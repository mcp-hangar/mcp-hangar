"""A browser preflight gets a CORS answer, not a 401.

Regression for #993. The served process wrapped the combined app (health +
/api + /mcp) with AuthEnforcementMiddleware directly; the CORS layer lived
only on the mounted api_app, inside auth. OPTIONS therefore hit auth first,
401'd with no Access-Control-Allow-Origin, and a browser OAuth client could
not call /mcp or /api at all -- for allowed and disallowed origins alike.

Two halves, both asserted here on the same stack the process serves
(CORSMiddleware OUTSIDE AuthEnforcementMiddleware, like lifecycle wires it):
auth skips OPTIONS (preflight carries no credentials by design -- the same
contract the authorization chokepoint already honors), and CORS wraps the
whole app so /mcp is covered too.
"""

from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from mcp_hangar.domain.exceptions import AuthenticationError
from mcp_hangar.server.api.middleware import AuthEnforcementMiddleware

_ORIGIN = "http://hangar.lab.local:8081"


class _RefusesEverything:
    def authenticate(self, request):
        raise AuthenticationError("no valid credentials")


def _client() -> TestClient:
    async def inner(scope, receive, send):
        # Stands in for the combined app; a preflight never reaches it when
        # CORSMiddleware answers, and a plain request without credentials
        # must be refused before it.
        await PlainTextResponse("served")(scope, receive, send)

    stack = CORSMiddleware(
        app=AuthEnforcementMiddleware(inner, authn=_RefusesEverything()),
        allow_origins=[_ORIGIN],
        allow_methods=["*"],
        allow_headers=["authorization", "content-type"],
    )
    return TestClient(stack, raise_server_exceptions=False)


def _preflight(client: TestClient, path: str, origin: str = _ORIGIN):
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


class TestPreflightIsAnsweredWithoutCredentials:
    def test_an_allowed_origin_gets_the_cors_answer_on_api(self):
        response = _preflight(_client(), "/api/system")
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == _ORIGIN

    def test_an_allowed_origin_gets_the_cors_answer_on_mcp(self):
        # /mcp never had a CORS stack at all -- the mounted api_app's copy
        # did not cover it.
        response = _preflight(_client(), "/mcp")
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == _ORIGIN

    def test_a_disallowed_origin_is_refused_by_cors_not_auth(self):
        response = _preflight(_client(), "/api/system", origin="http://evil.example")
        # CORSMiddleware answers the preflight (400 "Disallowed CORS origin"),
        # auth never speaks: the refusal must not be a credentials failure.
        assert response.status_code == 400
        assert "www-authenticate" not in response.headers

    def test_a_plain_request_without_credentials_is_still_401(self):
        # The OPTIONS skip must not widen: GET without credentials stays
        # refused, now with the CORS header a browser needs to READ the 401.
        response = _client().get("/api/system", headers={"Origin": _ORIGIN})
        assert response.status_code == 401
        assert response.headers.get("access-control-allow-origin") == _ORIGIN

    def test_a_bare_options_without_origin_reaches_the_app(self):
        # Not a preflight (no Origin): CORSMiddleware passes it through and
        # auth skips it -- a safe method with nothing to mutate behind it.
        response = _client().options("/api/system")
        assert response.status_code == 200


class TestTheServedProcessIsWiredThatWay:
    def test_lifecycle_wraps_the_combined_app_in_cors(self):
        # The defect was precisely that this wiring existed only on the
        # mounted api_app: the docstring said "CORS remains outermost" while
        # the served process wrapped the combined app with auth alone.
        import inspect

        from mcp_hangar.server import lifecycle

        source = inspect.getsource(lifecycle)
        assert "CORSMiddleware(app=starlette_app" in source, (
            "lifecycle no longer wraps the served (combined) app in CORSMiddleware -- "
            "preflight will 401 again and /mcp loses CORS headers (#993)"
        )
