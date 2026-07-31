"""Fixtures for live (black-box) feature verification.

These drive a *running* hangar the way a real client would -- over the shipped
CLI, HTTP surface, and MCP protocol -- rather than via internal Python APIs.
Everything here is opt-in: a fixture that cannot meet its prerequisites
(missing `mcp-hangar` on PATH, Docker/compose, Keycloak, a free port, or a
startup timeout) calls ``pytest.skip`` rather than failing, so the suite is
safe to run anywhere. See ``tests/live/README.md`` and the tier markers
(``live``/``t0``/``t1``/``t2``) registered in ``pyproject.toml``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
import os
import shutil
import socket
import subprocess
import sys
import time

import httpx
import pytest

from tests.live import _group_support as gs

# Opt-in gate: live verification only runs when explicitly requested, so a normal
# `pytest tests/` (incl. the release test run) never starts servers. The
# `live-verify` workflow sets this env var.
_OPT_IN_ENV = "MCP_HANGAR_LIVE_VERIFY"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(_OPT_IN_ENV) == "1":
        return
    skip_live = pytest.mark.skip(
        reason=f"live verification is opt-in: set {_OPT_IN_ENV}=1 (or run the live-verify workflow)"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# How long to wait for `mcp-hangar serve --http` to answer /health before skipping.
_STARTUP_TIMEOUT_S = 25.0
_POLL_INTERVAL_S = 0.3

# A minimal, backend-lazy config: one subprocess provider (math) that stays cold
# until invoked, so the server starts and serves /health and /metrics without
# any external dependency. T0 tests that actually invoke a tool drive this one.
_MATH_SERVER = Path(__file__).resolve().parents[2] / "examples" / "provider_math" / "server.py"

_MINIMAL_CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
"""


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _hangar_bin() -> str:
    binary = shutil.which("mcp-hangar")
    if binary is None:
        pytest.skip("`mcp-hangar` not on PATH (run under `uv run`); live harness unavailable")
    return binary


def _serve_hangar(workdir: Path, config_text: str) -> Iterator[str]:
    """Start `mcp-hangar serve --http` with ``config_text`` and yield its base URL.

    Shared engine for every "run a real hangar over HTTP" fixture. Writes the
    config into ``workdir``, binds a free loopback port, polls ``/health/live``
    until healthy, then yields the base URL and tears the process down on exit.
    Skips cleanly (never fails) if the binary is missing or the server does not
    become healthy within the startup budget.
    """
    binary = _hangar_bin()

    config_path = workdir / "config.yaml"
    config_path.write_text(config_text)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [binary, "--config", str(config_path), "serve", "--http", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(workdir),
    )

    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    healthy = False
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:  # process exited before becoming healthy
                break
            try:
                # `serve --http` exposes liveness at /health/live (the operational probe).
                if httpx.get(f"{base_url}/health/live", timeout=1.0).status_code == 200:
                    healthy = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(_POLL_INTERVAL_S)

        if not healthy:
            proc.terminate()
            out = b""
            try:
                out = proc.communicate(timeout=5)[0] or b""
            except subprocess.TimeoutExpired:
                proc.kill()
            pytest.skip(
                f"hangar did not become healthy in {_STARTUP_TIMEOUT_S}s:\n{out.decode(errors='replace')[-2000:]}"
            )

        yield base_url
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="session")
def live_http_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start `mcp-hangar serve --http` on loopback and yield its base URL.

    Skips cleanly if the binary is missing or the server does not become healthy
    within the startup budget. Loopback binding needs no auth.
    """
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("live_hangar")
    yield from _serve_hangar(workdir, _MINIMAL_CONFIG.format(python=sys.executable, server=str(_MATH_SERVER)))


# --- T2: live OIDC / Keycloak (real IdP) --------------------------------------
#
# These fixtures drive a REAL Keycloak (issuer A = realm `mcp-hangar`, issuer B =
# realm `mcp-hangar-b`) and a REAL hangar with OIDC auth enabled. Everything is
# skip-safe: if Keycloak or Docker is unavailable, tests SKIP (never fail). We
# reuse an already-running Keycloak and never tear down an IdP we did not start.

# Keycloak base URL: reuse an already-running instance; override for elsewhere.
_KEYCLOAK_BASE_URL = os.environ.get("MCP_HANGAR_KEYCLOAK_URL", "http://localhost:8080")
_REALM_A = "mcp-hangar"
_REALM_B = "mcp-hangar-b"
_COMPOSE_FILE = Path(__file__).resolve().parents[2] / "examples" / "auth-keycloak" / "docker-compose.yml"
_KEYCLOAK_START_TIMEOUT_S = 120.0


def _realm_discovery_ok(base_url: str, realm: str) -> bool:
    """Return True if the realm's OIDC discovery endpoint answers 200."""
    try:
        resp = httpx.get(f"{base_url}/realms/{realm}/.well-known/openid-configuration", timeout=2.0)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _realms_ready(base_url: str) -> bool:
    return _realm_discovery_ok(base_url, _REALM_A) and _realm_discovery_ok(base_url, _REALM_B)


@pytest.fixture(scope="session")
def keycloak_base_url() -> str:
    """Return a reachable Keycloak base URL exposing realms A and B, else skip.

    Reuses an already-running Keycloak (never torn down here). If none answers,
    tries ``docker compose up -d keycloak`` from the auth-keycloak example and
    waits for both realms to import. Skips (never fails) when Docker or Keycloak
    are unavailable or the required realms never appear.
    """
    base_url = _KEYCLOAK_BASE_URL
    if _realms_ready(base_url):
        return base_url

    # Not reachable: attempt to start it from the shipped compose file. We only
    # ever start it here; teardown of a Keycloak we started is left to the
    # operator (compose keeps it running for reuse across the suite).
    if shutil.which("docker") is None or not _COMPOSE_FILE.exists():
        pytest.skip(f"Keycloak not reachable at {base_url}; Docker/compose unavailable to start it")
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(_COMPOSE_FILE), "up", "-d", "keycloak"],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        pytest.skip(f"could not start Keycloak via docker compose: {exc}")

    deadline = time.monotonic() + _KEYCLOAK_START_TIMEOUT_S
    while time.monotonic() < deadline:
        if _realms_ready(base_url):
            return base_url
        time.sleep(2.0)
    pytest.skip(f"Keycloak at {base_url} did not import realms {_REALM_A!r} + {_REALM_B!r} in time")


@pytest.fixture(scope="session")
def keycloak_token(keycloak_base_url: str):
    """Return a helper that mints real access tokens via the OIDC password grant.

    Usage: ``keycloak_token(realm, username, password, client_id="mcp-cli")``.
    Skips (never fails) if the grant does not succeed.
    """

    def _mint(realm: str, username: str, password: str, client_id: str = "mcp-cli") -> str:
        resp = httpx.post(
            f"{keycloak_base_url}/realms/{realm}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": client_id,
                "username": username,
                "password": password,
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            pytest.skip(f"password grant failed for {realm}/{username}: {resp.status_code} {resp.text[:200]}")
        token = resp.json().get("access_token")
        if not token:
            pytest.skip(f"no access_token in token response for {realm}/{username}")
        return str(token)

    return _mint


# Hangar config trusting both Keycloak realms. front_door topology + auth on,
# OIDC multi-issuer registry, audience/resource bound to `mcp-hangar` (RFC 8707),
# require_tenant on (so a token with no tenant claim is rejected fail-closed).
_OIDC_CONFIG = """\
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
    resource_uri: "{resource}"
    require_tenant: true
    issuers:
      - issuer: {issuer_a}
      - issuer: {issuer_b}
  role_assignments:
    - principal: "group:developers"
      role: developer
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
"""


def _oidc_config_text(keycloak_base_url: str, resource: str) -> str:
    return _OIDC_CONFIG.format(
        resource=resource,
        issuer_a=f"{keycloak_base_url}/realms/{_REALM_A}",
        issuer_b=f"{keycloak_base_url}/realms/{_REALM_B}",
        python=sys.executable,
        server=str(_MATH_SERVER),
    )


@pytest.fixture(scope="session")
def hangar_oidc(keycloak_base_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run a real hangar (front_door + auth) trusting Keycloak realms A and B.

    Resource/audience bound to ``mcp-hangar`` (matches the tokens' aud), OIDC
    multi-issuer registry (A + B), ``require_tenant`` on. Yields the base URL;
    skip-safe.
    """
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("hangar_oidc")
    yield from _serve_hangar(workdir, _oidc_config_text(keycloak_base_url, resource="mcp-hangar"))


@pytest.fixture(scope="session")
def hangar_oidc_wrong_audience(keycloak_base_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Same as ``hangar_oidc`` but expecting a DIFFERENT resource/audience.

    A real realm-A token (aud=``mcp-hangar``) must fail audience validation here,
    exercising RFC 8707 resource binding. Yields the base URL; skip-safe.
    """
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("hangar_oidc_wrong_aud")
    yield from _serve_hangar(workdir, _oidc_config_text(keycloak_base_url, resource="urn:mcp-hangar:other-resource"))


# --------------------------------------------------------------------------- #
# T1: multi-backend group + per-tenant canary harness
# --------------------------------------------------------------------------- #
#
# Unlike the T0 stub (``examples/provider_math``, whose results are anonymous),
# the group members here run ``examples/provider_identity`` -- a stdio backend
# whose ``whoami`` tool echoes *which* instance served the call. Two members
# (``member-a`` / ``member-b``) run as ``mode: subprocess`` group members, so a
# live ``hangar_call`` to the group can be observed landing on a real backend and
# the serving member asserted.
#
# For canary/version routing the caller's tenant must reach hangar. The shipped,
# no-IdP way to carry an arbitrary per-request tenant over the HTTP surface is an
# API key (``X-API-Key``) whose stored principal carries a ``tenant_id``; keys are
# seeded with the shipped ``SQLiteApiKeyStore`` and the server points its
# ``auth.storage`` at the same DB. Reusable pieces live in ``_group_support``.


@pytest.fixture(scope="session")
def live_group_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[gs.GroupHarness]:
    """Start hangar with a 2-member group + canary policy; yield a GroupHarness.

    Skips cleanly if the binary or identity stub is missing, the server does not
    become healthy, or the group never warms a member within the startup budget.
    """
    binary = _hangar_bin()
    if not gs.IDENTITY_SERVER.exists():
        pytest.skip(f"identity stub backend not found at {gs.IDENTITY_SERVER}")

    workdir = tmp_path_factory.mktemp("live_group")
    auth_db = workdir / "auth.db"

    try:
        tenant_keys = gs.seed_tenant_keys(auth_db, [*gs.PINNED, *gs.SPLIT_TENANTS])
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed tenant API keys: {exc}")

    config_path = workdir / "config.yaml"
    config_path.write_text(gs.render_config(auth_db))

    port = _free_port()
    harness = gs.GroupHarness(base_url=f"http://127.0.0.1:{port}", tenant_keys=tenant_keys)
    proc = subprocess.Popen(
        [binary, "--config", str(config_path), "serve", "--http", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(workdir),
    )

    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    healthy = False
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                if httpx.get(f"{harness.base_url}/health/live", timeout=1.0).status_code == 200:
                    healthy = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(_POLL_INTERVAL_S)

        if not healthy:
            proc.terminate()
            out = b""
            try:
                out = proc.communicate(timeout=5)[0] or b""
            except subprocess.TimeoutExpired:
                proc.kill()
            pytest.skip(
                f"group hangar did not become healthy in {_STARTUP_TIMEOUT_S}s:\n{out.decode(errors='replace')[-2000:]}"
            )

        # Warm the group: members cold-start on first use, so poll until one serves.
        warm_deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        warmed = False
        while time.monotonic() < warm_deadline:
            try:
                if gs.serving_member(harness) in harness.members:
                    warmed = True
                    break
            except Exception:  # noqa: BLE001 -- transient during member cold-start
                pass
            time.sleep(_POLL_INTERVAL_S)

        if not warmed:
            pytest.skip(f"group '{gs.GROUP_ID}' never warmed a member in {_STARTUP_TIMEOUT_S}s")

        yield harness
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
