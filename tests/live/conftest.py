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
