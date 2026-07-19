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


@pytest.fixture(scope="session")
def live_http_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start `mcp-hangar serve --http` on loopback and yield its base URL.

    Skips cleanly if the binary is missing or the server does not become healthy
    within the startup budget. Loopback binding needs no auth.
    """
    binary = _hangar_bin()
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("live_hangar")
    config_path = workdir / "config.yaml"
    config_path.write_text(_MINIMAL_CONFIG.format(python=sys.executable, server=str(_MATH_SERVER)))

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


# ---------------------------------------------------------------------------
# Tier 2 (auth / IdP) harness: a real Keycloak from examples/auth-keycloak/,
# fronted by `mcp-hangar serve --http` with OIDC auth enabled. Every fixture
# skips cleanly when its prerequisite (Docker, a reachable Keycloak, the hangar
# binary, a healthy startup) is absent, so this stays safe to run anywhere.
# ---------------------------------------------------------------------------

_KEYCLOAK_DIR = Path(__file__).resolve().parents[2] / "examples" / "auth-keycloak"
_KEYCLOAK_COMPOSE = _KEYCLOAK_DIR / "docker-compose.yml"
_KEYCLOAK_URL = "http://localhost:8080"
_REALM = "mcp-hangar"
_ISSUER = f"{_KEYCLOAK_URL}/realms/{_REALM}"
_TOKEN_URL = f"{_ISSUER}/protocol/openid-connect/token"
_KEYCLOAK_READY_TIMEOUT_S = 180.0

# Users seeded by keycloak/realm-export.json (username, password, group -> role).
KEYCLOAK_USERS = {
    "admin": "admin123",  # group platform-engineering -> admin
    "developer": "dev123",  # group developers          -> developer
    "viewer": "view123",  # group viewers             -> viewer
}

# Hangar auth config that matches the exported realm (front-door: anonymous
# denied, OIDC issuer trusted, groups claim mapped to roles).
_AUTH_CONFIG = """\
logging:
  level: WARNING
auth:
  enabled: true
  allow_anonymous: false
  oidc:
    enabled: true
    issuer: {issuer}
    audience: mcp-hangar
    groups_claim: groups
  role_assignments:
    - principal: "group:platform-engineering"
      role: admin
      scope: global
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


def _compose_cmd() -> list[str]:
    """Return a working `docker compose` (v2) or `docker-compose` invocation, or skip."""
    if shutil.which("docker") and subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    pytest.skip("Docker Compose not available; T2 Keycloak harness unavailable")


@pytest.fixture(scope="session")
def keycloak() -> Iterator[str]:
    """Bring up the example Keycloak and yield its issuer URL.

    Skips (never fails) if Docker/Compose is missing or Keycloak does not become
    ready within the budget. Reuses examples/auth-keycloak/ verbatim.
    """
    if not _KEYCLOAK_COMPOSE.exists():
        pytest.skip(f"Keycloak compose not found at {_KEYCLOAK_COMPOSE}")
    compose = _compose_cmd()
    base = [*compose, "-f", str(_KEYCLOAK_COMPOSE)]

    # Only the keycloak service (not the compose's hangar service, which would
    # build an image); hangar runs on the host via the CLI.
    up = subprocess.run([*base, "up", "-d", "keycloak"], capture_output=True, text=True)
    if up.returncode != 0:
        pytest.skip(f"could not start Keycloak:\n{up.stderr[-2000:]}")

    try:
        discovery = f"{_ISSUER}/.well-known/openid-configuration"
        deadline = time.monotonic() + _KEYCLOAK_READY_TIMEOUT_S
        ready = False
        while time.monotonic() < deadline:
            try:
                if httpx.get(discovery, timeout=2.0).status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1.0)
        if not ready:
            logs = subprocess.run([*base, "logs", "keycloak"], capture_output=True, text=True)
            pytest.skip(f"Keycloak not ready in {_KEYCLOAK_READY_TIMEOUT_S}s:\n{logs.stdout[-2000:]}")
        yield _ISSUER
    finally:
        subprocess.run([*base, "down", "-v"], capture_output=True)


@pytest.fixture(scope="session")
def keycloak_token(keycloak: str):
    """Return a helper that fetches an access token via the password grant."""

    def _token(username: str, password: str | None = None) -> str:
        password = password or KEYCLOAK_USERS[username]
        resp = httpx.post(
            _TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": "mcp-hangar",
                "client_secret": "mcp-hangar-secret",
                "username": username,
                "password": password,
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            pytest.skip(f"token request for {username} failed ({resp.status_code}): {resp.text[:500]}")
        return str(resp.json()["access_token"])

    return _token


@pytest.fixture(scope="session")
def auth_http_hangar(keycloak: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start `mcp-hangar serve --http` with OIDC auth trusting the example realm."""
    binary = _hangar_bin()
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("live_auth_hangar")
    config_path = workdir / "config.yaml"
    config_path.write_text(_AUTH_CONFIG.format(issuer=keycloak, python=sys.executable, server=str(_MATH_SERVER)))

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
            if proc.poll() is not None:
                break
            try:
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
                f"auth hangar did not become healthy in {_STARTUP_TIMEOUT_S}s:\n{out.decode(errors='replace')[-2000:]}"
            )
        yield base_url
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
