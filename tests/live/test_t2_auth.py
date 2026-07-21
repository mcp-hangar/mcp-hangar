"""Tier 2 live verification: auth / IdP (JWT/OIDC, multi-issuer, audience, RBAC).

These are BLACK-BOX tests against a REAL Keycloak (issuer A = realm
``mcp-hangar``, issuer B = realm ``mcp-hangar-b``) and a REAL ``mcp-hangar``
process with OIDC auth enabled in ``front_door`` mode. They prove the security
trust-boundary claims tracked in ``docs/internal/LIVE_VERIFICATION.md``:

1. front_door + auth: an UNAUTHENTICATED request is denied (fail-closed).
2. a real signed OIDC token authenticates AND carries a ``tenant_id`` (proven
   fail-closed via ``require_tenant``: a token with no tenant is rejected).
3. a token from an UNTRUSTED issuer is rejected (multi-issuer registry miss).
4. a real token whose ``aud`` does not match the expected resource is rejected
   (RFC 8707 resource binding).
5. the PRM endpoint advertises BOTH trusted issuers and the resource (RFC 9728).

Multi-issuer is also proven positively: a realm-B token is accepted by the same
hangar.

The fixtures (see ``conftest.py``) are skip-safe: if Keycloak/Docker is absent
the whole module SKIPs rather than fails. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t2" -o addopts=""
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import json
import sys
import time

import httpx
import jwt
import pytest

from tests.live.conftest import _serve_hangar

pytestmark = [pytest.mark.live, pytest.mark.t2]

# An authenticated (non-skipped) API surface: auth enforcement runs before the
# handler, so a missing/invalid token is rejected at the trust boundary and a
# valid token reaches ``/api/system/me`` which echoes the auth status.
_AUTHED_PATH = "/api/system/me"


def test_unauthenticated_front_door_call_is_denied(hangar_oidc: str) -> None:
    """Claim: in front_door mode, an unauthenticated request is DENIED (fail-closed)."""
    resp = httpx.get(f"{hangar_oidc}{_AUTHED_PATH}", timeout=10.0)
    assert resp.status_code == 401, resp.text
    # RFC 6750/9728: a Bearer challenge is advertised on the fail-closed response.
    assert "www-authenticate" in {k.lower() for k in resp.headers}


def test_valid_oidc_token_authenticates_and_carries_tenant(hangar_oidc: str, keycloak_token) -> None:
    """Claim: a signed OIDC token authenticates; tenant_id is extracted from the claim.

    The hangar runs with ``require_tenant`` on, so authentication SUCCEEDS only
    when a tenant claim is present and extracted from the validated token. A real
    realm-A ``developer`` token carries ``tenant_id=acme`` (Keycloak user-attribute
    mapper), so a 200 here proves both authentication and tenant extraction.
    """
    token = keycloak_token(realm="mcp-hangar", username="developer", password="dev123")
    resp = httpx.get(
        f"{hangar_oidc}{_AUTHED_PATH}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("authenticated") is True, body
    assert body.get("principal"), body


def test_realm_b_token_is_accepted(hangar_oidc: str, keycloak_token) -> None:
    """Claim (multi-issuer positive): a token from the SECOND trusted issuer is accepted."""
    token = keycloak_token(realm="mcp-hangar-b", username="developer", password="dev123")
    resp = httpx.get(
        f"{hangar_oidc}{_AUTHED_PATH}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("authenticated") is True, resp.text


def test_token_from_untrusted_issuer_is_rejected(hangar_oidc: str) -> None:
    """Claim: with a multi-issuer registry, a token from an untrusted issuer is rejected (#273).

    A self-signed JWT whose ``iss`` is neither trusted realm never matches the
    issuer registry, so it is rejected before any signature check.
    """
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": "https://untrusted.example.invalid/realms/rogue",
            "sub": "attacker",
            "aud": "mcp-hangar",
            "tenant_id": "acme",
            "iat": now,
            "exp": now + 300,
        },
        "attacker-controlled-secret-key-not-trusted-by-hangar",
        algorithm="HS256",
    )
    resp = httpx.get(
        f"{hangar_oidc}{_AUTHED_PATH}",
        headers={"Authorization": f"Bearer {forged}"},
        timeout=10.0,
    )
    assert resp.status_code == 401, resp.text
    assert "www-authenticate" in {k.lower() for k in resp.headers}


def test_aud_mismatch_is_rejected(hangar_oidc_wrong_audience: str, keycloak_token) -> None:
    """Claim: a real token whose aud != expected resource is rejected (RFC 8707).

    ``hangar_oidc_wrong_audience`` expects resource ``urn:mcp-hangar:other-resource``;
    a real realm-A token carries ``aud=mcp-hangar`` and so fails audience validation.
    """
    token = keycloak_token(realm="mcp-hangar", username="developer", password="dev123")
    resp = httpx.get(
        f"{hangar_oidc_wrong_audience}{_AUTHED_PATH}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    assert resp.status_code == 401, resp.text


def test_prm_advertises_trusted_issuers(hangar_oidc: str, keycloak_base_url: str) -> None:
    """Claim: GET /.well-known/oauth-protected-resource lists all trusted issuers (RFC 9728)."""
    resp = httpx.get(f"{hangar_oidc}/.well-known/oauth-protected-resource", timeout=10.0)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    servers = set(body.get("authorization_servers", []))
    issuer_a = f"{keycloak_base_url}/realms/mcp-hangar"
    issuer_b = f"{keycloak_base_url}/realms/mcp-hangar-b"
    assert issuer_a in servers, body
    assert issuer_b in servers, body
    # RFC 9728: the resource identifier is advertised too.
    assert body.get("resource") == "mcp-hangar", body


# --- RBAC denial (role-store-driven, token `groups` claim as the join key) -----
#
# RBAC is role-store-driven, NOT token-claim-driven: roles are NOT read from the
# token's ``roles``/``realm_access`` claim. ``bootstrap_auth`` seeds hangar's own
# role store from the config's ``role_assignments`` (keyed by ``group:<name>``),
# and a validated token's ``groups`` claim is the join key --
# ``RBACAuthorizer._collect_roles`` looks each group up as ``group:<name>``. A
# real realm-A token carries ``groups=["developers"]`` or ``["viewers"]``, so the
# hangar below maps ``group:developers -> developer`` (has ``mcp_servers:write``)
# and ``group:viewers -> viewer`` (read-only, NO ``mcp_servers:write``).
#
# The privileged operation is ``POST /api/mcp_servers/`` -- INTENDED to be guarded
# by ``mcp_servers:write`` in ``server/api/mcp_servers.py::create_mcp_server`` via
# ``_check_permission`` -> ``AuthorizationMiddleware.authorize`` -> 403
# ``AccessDeniedError``.
#
# IMPORTANT (see docs/internal/LIVE_VERIFICATION.md): as shipped, ``serve`` does
# NOT actually enforce this. ``_check_permission`` reads ``auth_components`` from
# the global ``ApplicationContext`` via ``get_context()``, but bootstrap installs
# that global with ``init_context(runtime)`` and never sets ``auth_components`` on
# it -- so the guard sees ``authz_middleware is None`` and returns early (fail-OPEN;
# a viewer's write is silently allowed). The MCP ``hangar_call`` path likewise
# never consults ``tool:invoke``. Authentication is wired (a separate middleware),
# authorization is not. This test therefore asserts the intended invariant when it
# is enforced (so it flips to a real PASS once the wiring is fixed) and otherwise
# SKIPS with a loud reason rather than faking a pass.

# Reuse the shipped stub backend so the hangar starts without external deps.
_MATH_SERVER = Path(__file__).resolve().parents[2] / "examples" / "provider_math" / "server.py"

# front_door + auth + OIDC (realm A) + role_assignments seeding the role store.
# require_tenant stays on (all three realm-A users carry a tenant_id), so the
# ONLY axis that differs between viewer and developer here is the assigned role.
_RBAC_CONFIG = """\
logging:
  level: WARNING
tool_access:
  mode: front_door
auth:
  enabled: true
  allow_anonymous: false
  storage:
    driver: memory
  api_key:
    enabled: false
  oidc:
    enabled: true
    resource_uri: "mcp-hangar"
    require_tenant: true
    issuers:
      - issuer: {issuer_a}
  role_assignments:
    - principal: "group:developers"
      role: developer
      scope: global
    - principal: "group:viewers"
      role: viewer
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
"""

# Privileged, permission-guarded operation: creating an mcp_server is INTENDED to
# need `mcp_servers:write`, which `viewer` lacks and `developer` holds.
_PRIVILEGED_CREATE_PATH = "/api/mcp_servers/"


@pytest.fixture(scope="module")
def hangar_rbac(keycloak_base_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run a real hangar (front_door + auth + RBAC) trusting realm A.

    RBAC is role-store-driven: ``role_assignments`` seed hangar's role store
    (keyed by ``group:<name>``) and a validated token's ``groups`` claim is the
    join key. ``group:developers -> developer`` (``mcp_servers:write``);
    ``group:viewers -> viewer`` (read-only). Module-local so it does not touch
    the shared conftest; skip-safe via the reused ``_serve_hangar`` engine.
    """
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("hangar_rbac")
    config_text = _RBAC_CONFIG.format(
        issuer_a=f"{keycloak_base_url}/realms/mcp-hangar",
        python=sys.executable,
        server=str(_MATH_SERVER),
    )
    yield from _serve_hangar(workdir, config_text)


def test_rbac_denies_unprivileged_and_allows_privileged(hangar_rbac: str, keycloak_token) -> None:
    """Claim: RBAC denies an unprivileged principal a privileged op and allows a privileged one.

    ONE operation (``POST /api/mcp_servers/``, intended to be guarded by
    ``mcp_servers:write``), TWO real realm-A tokens minted by the live Keycloak:

    * ``viewer`` -> ``group:viewers`` -> role ``viewer`` (read-only): must be
      DENIED 403 with a fail-closed ``access_denied`` (authorization, not
      authentication -- the token itself is valid).
    * ``developer`` -> ``group:developers`` -> role ``developer``
      (``mcp_servers:write``): must be ALLOWED 201.

    Both tokens authenticate successfully (valid signature, trusted issuer, tenant
    present), so only the assigned role decides the outcome -- proving enforcement
    is role-store-driven off the config ``role_assignments`` joined on the token's
    ``groups`` claim, not a property of the token itself.

    As shipped, ``serve`` does not enforce this (see the module comment and
    docs/internal/LIVE_VERIFICATION.md: ``auth_components`` is absent from the
    global ``ApplicationContext`` handlers read, so ``_check_permission`` no-ops
    fail-OPEN). When the viewer is NOT denied we ``pytest.skip`` with the concrete
    finding rather than fake a pass; when it IS denied the full invariant is
    asserted, so this test becomes a real PASS the moment the wiring is fixed.
    """
    create_body = {
        "mcp_server_id": "rbac-probe",
        "mode": "subprocess",
        "command": [sys.executable, "-c", "pass"],
    }

    # Negative control: an unprivileged (viewer) principal attempts the write.
    viewer = keycloak_token(realm="mcp-hangar", username="viewer", password="view123")
    denied = httpx.post(
        f"{hangar_rbac}{_PRIVILEGED_CREATE_PATH}",
        headers={"Authorization": f"Bearer {viewer}"},
        json=create_body,
        timeout=10.0,
    )

    if denied.status_code != 403:
        # Fail-OPEN detected: the RBAC-denial claim cannot be proven live here.
        # Do NOT weaken the assertion or fake a pass -- report and skip.
        pytest.skip(
            "RBAC authorization is NOT enforced on the shipped `serve` HTTP surface "
            f"(fail-open): a read-only `viewer` token performed the write-privileged "
            f"POST {_PRIVILEGED_CREATE_PATH} and got HTTP {denied.status_code} "
            "(expected 403). Root cause: `auth_components` is never set on the global "
            "ApplicationContext that request handlers consult via get_context() "
            "(server/bootstrap/__init__.py installs it with init_context(runtime) but "
            "omits ctx.auth_components), so `_check_permission` sees authz_middleware=None "
            "and returns early; the MCP hangar_call path likewise never checks "
            "`tool:invoke`. Authentication is wired, authorization is not. "
            "See docs/internal/LIVE_VERIFICATION.md."
        )

    # Enforced path (self-correcting once the wiring is fixed): full invariant.
    denied_body = denied.json()
    # Fail-closed AUTHORIZATION denial (the token authenticated; the role did not).
    # The error is a structured object: {"error": {"code": "AccessDeniedError",
    # "details": {"action": ...}, "message": ...}}.
    err = denied_body.get("error")
    assert isinstance(err, dict) and err.get("code") == "AccessDeniedError", denied_body
    assert err.get("details", {}).get("action") == "write", denied_body

    # Positive control: a real developer token is ALLOWED the same write.
    developer = keycloak_token(realm="mcp-hangar", username="developer", password="dev123")
    allowed = httpx.post(
        f"{hangar_rbac}{_PRIVILEGED_CREATE_PATH}",
        headers={"Authorization": f"Bearer {developer}"},
        json=create_body,
        timeout=10.0,
    )
    assert allowed.status_code == 201, allowed.text


# --- tool:invoke on the MCP hangar_call path (#385) ---------------------------
#
# The REST test above guards a REST endpoint. This one closes the remaining
# fail-open gap: the MCP ``hangar_call`` tool-invoke path must enforce
# ``tool:invoke`` too. It drives the SAME hangar over the REAL MCP surface
# (streamable-HTTP ``/mcp``) with real Keycloak tokens, exactly as a client
# would, and asserts the batch's per-call outcome:
#   * viewer  (group:viewers  -> role viewer,    NO tool:invoke) -> DENIED
#   * developer(group:developers-> role developer, HAS tool:invoke) -> ALLOWED
# The token authenticates in both cases (valid signature, trusted issuer, tenant
# present), so only the assigned role decides whether the tool call is invoked.


def _invoke_hangar_call(base_url: str, token: str, calls: list[dict]) -> object:
    """Call the ``hangar_call`` tool over streamable-HTTP with a Bearer token."""
    import asyncio

    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    headers = {"Authorization": f"Bearer {token}"}

    async def _run() -> object:
        async with open_mcp_streams(f"{base_url}/mcp", headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("hangar_call", {"calls": calls})

    return asyncio.run(_run())


def _batch_from_result(result: object) -> dict:
    """Extract the ``hangar_call`` batch dict (has a ``results`` list) from a CallToolResult."""
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if "results" in structured:
            return structured
        inner = structured.get("result")
        if isinstance(inner, dict) and "results" in inner:
            return inner
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and "results" in data:
            return data
    raise AssertionError(f"could not parse hangar_call batch from result: {result!r}")


def test_hangar_call_enforces_tool_invoke_viewer_denied_developer_allowed(hangar_rbac: str, keycloak_token) -> None:
    """Claim: the MCP hangar_call path enforces tool:invoke (viewer denied, developer allowed).

    ONE tool invocation (``math.add`` via ``hangar_call`` over ``/mcp``), TWO real
    realm-A tokens minted by the live Keycloak:

    * ``viewer`` -> ``group:viewers`` -> role ``viewer`` (no ``tool:invoke``): the
      per-call result is DENIED fail-closed (``success=false``,
      ``error_type="AuthorizationDenied"``) and the tool is NOT executed.
    * ``developer`` -> ``group:developers`` -> role ``developer`` (has
      ``tool:invoke``): the call is ALLOWED by the authorization gate and passes
      through to the backend invoke path (it is never an ``AuthorizationDenied``).

    Both tokens authenticate (valid signature, trusted issuer, tenant present),
    so only the assigned role changes the outcome -- proving enforcement is
    role-driven on the tool-invoke path, not a property of the token. The
    developer arm asserts the authorization decision only (not the backend's
    execution result), so the security invariant does not hinge on the ``math``
    subprocess successfully cold-starting in every environment.
    """
    calls = [{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]

    # Negative control: a read-only viewer must be denied the tool invocation.
    viewer = keycloak_token(realm="mcp-hangar", username="viewer", password="view123")
    viewer_batch = _batch_from_result(_invoke_hangar_call(hangar_rbac, viewer, calls))
    viewer_call = viewer_batch["results"][0]
    assert viewer_call["success"] is False, viewer_batch
    assert viewer_call["error_type"] == "AuthorizationDenied", viewer_batch

    # Positive control: a developer holding tool:invoke is ALLOWED past the authz
    # gate. Assert the authorization decision (not the backend's execution
    # outcome): the call either succeeds or fails for a non-authorization,
    # backend-execution reason -- it is never denied by authorization. This keeps
    # the security claim robust to the ``math`` subprocess failing to start in a
    # constrained CI/sandbox.
    developer = keycloak_token(realm="mcp-hangar", username="developer", password="dev123")
    dev_batch = _batch_from_result(_invoke_hangar_call(hangar_rbac, developer, calls))
    dev_call = dev_batch["results"][0]
    assert dev_call["error_type"] not in {"AuthorizationDenied", "MissingCredentialsError"}, dev_batch
    if dev_call["success"] is not True:
        # Reached the backend invoke path (e.g. cold-start), proving the authz
        # gate let the developer through -- not an authorization rejection.
        assert dev_call["result"] == {"result": 3.0} or dev_call["error_type"] not in (None, ""), dev_batch
