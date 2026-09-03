"""The exit gate of the 2.18.0 funnel, driven by a real MCP client (#1189).

Three claims are asserted here, over stdio, through the SDK's own
`ClientSession` against `mcp-hangar serve` in a subprocess -- not against
objects assembled in-process:

1. with `auth.stdio.principal`, `front_door` serves the upstream's own flat tool
   names, and one of them can be called;
2. without it, the same configuration serves **zero** tools and logs
   `no_identity` -- the fail-closed behaviour #902 built, which must not be
   loosened by ADR-026;
3. with a pin that does not match, the same call is refused with the
   digest-mismatch error.

Why not unit tests: every one of these has an in-process version already, and
none of them would have caught the two defects this file exists to prevent. The
identity binding depends on which task and thread the contextvar is read in; the
projection depends on a real `initialize` handshake; and the refusal depends on
the executor being reached through the flat dispatcher rather than `hangar_call`.
A test that constructs the projection directly proves none of that.

The release smoke (`scripts/smoke_published_artifact.py`) walks the same ground
against the published wheel. This runs on every PR, which is where a regression
is cheap to fix.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

# The quickstart's own upstream, so this test and the documented walkthrough
# exercise the same server. `tests/mock_provider.py` is not usable here: it
# answers `tools/call` with `{"result": 5}`, which is not a `CallToolResult`, and
# a real SDK client rejects the envelope before any of this can be asserted.
UPSTREAM = str(Path(__file__).resolve().parent.parent.parent / "examples" / "rugpull" / "server.py")


def _hangar_binary() -> str | None:
    """The console script that belongs to the interpreter running the tests.

    `shutil.which` alone is wrong here: it answers from PATH, which in a venv
    invoked as `.venv/bin/python -m pytest` does not include the venv's own
    `bin/`. That made this file skip silently on exactly the setup most people
    run it in -- a green suite that asserted nothing.
    """
    beside = Path(sys.executable).parent / ("mcp-hangar.exe" if sys.platform == "win32" else "mcp-hangar")
    if beside.is_file():
        return str(beside)
    return shutil.which("mcp-hangar")


HANGAR = _hangar_binary()

pytestmark = pytest.mark.skipif(
    HANGAR is None,
    reason="the `mcp-hangar` console script is not installed for this interpreter",
)

# The driver runs in its own process for one reason: the SDK's stdio client owns
# the lifetime of the gateway subprocess, and pytest's event loop and captured
# stdio make that ownership hard to reason about. A subprocess also means the
# assertions below read exactly what a real client received, as JSON.
DRIVER = """\
import json, sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BINARY, CONFIG, TOOL, RETRIES = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])


async def main() -> None:
    params = StdioServerParameters(command=BINARY, args=["--config", CONFIG, "serve"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = []
            # front_door warms every configured server at boot; the first listing
            # can land before that finishes, which is a race and not a verdict.
            for _ in range(RETRIES):
                names = sorted(t.name for t in (await session.list_tools()).tools)
                if TOOL in names:
                    break
                await anyio.sleep(0.5)

            called = None
            if TOOL in names:
                result = await session.call_tool(TOOL, {"text": "hi"})
                dumped = result.model_dump(mode="json")
                called = {
                    "is_error": bool(dumped.get("isError") or dumped.get("is_error")),
                    "content": json.dumps(dumped.get("content"), default=str),
                }
            print(json.dumps({"tools": names, "called": called}))


anyio.run(main)
"""

CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{provider}"]
    idle_ttl_s: 60
{pins}tool_access:
  mode: front_door
{auth}"""

PRINCIPAL = """\
auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [viewer]
"""


def write_config(tmp_path: Path, *, principal: bool, pin: str | None = None) -> Path:
    pins = ""
    if pin is not None:
        # The digest is quoted on purpose: an all-digit pin is valid hex and
        # YAML would hand it over as an int, which the loader then drops with
        # `invalid_config_digest_pin`.
        pins = f'    tool_projection:\n      digest_enforcement: block\n      pins:\n        echo: "{pin}"\n'
    config = tmp_path / "config.yaml"
    config.write_text(
        CONFIG.format(
            python=sys.executable,
            provider=UPSTREAM,
            pins=pins,
            auth=PRINCIPAL if principal else "",
        )
    )
    return config


def drive(tmp_path: Path, config: Path, tool: str = "echo", retries: int = 30) -> dict:
    """Run one client session against `mcp-hangar serve` and return what it saw.

    `retries` is how long the client waits for the tool to appear. It is a knob
    because the two directions cost differently: waiting is right when the tool
    is expected (front_door warms its servers at boot, and a listing can land
    first), and pure delay when the answer under test is an empty list.
    """
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    result = subprocess.run(
        [sys.executable, str(driver), HANGAR, str(config), tool, str(retries)],
        capture_output=True,
        text=True,
        # Under the 60s pytest-timeout the CI job applies, so a hung gateway
        # fails with this test's own message and the client's output attached,
        # rather than being killed with neither.
        timeout=45,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"the client failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_declared_principal_gets_the_upstreams_own_tools(tmp_path: Path):
    answered = drive(tmp_path, write_config(tmp_path, principal=True))

    assert "echo" in answered["tools"], answered
    # The management surface follows `roles: [viewer]`: reads, nothing that
    # changes state.
    assert "hangar_status" in answered["tools"]
    assert "hangar_stop" not in answered["tools"]
    # And a flat call works, even though `viewer` holds no `tool:invoke` --
    # that permission gates `hangar_call`, not this path.
    assert answered["called"] is not None
    assert answered["called"]["is_error"] is False, answered["called"]


def test_without_a_principal_the_front_door_stays_empty(tmp_path: Path):
    # The regression guard for #902. ADR-026 named a caller; it did not make an
    # unnamed one welcome.
    answered = drive(tmp_path, write_config(tmp_path, principal=False), retries=1)

    assert answered["tools"] == [], answered
    assert answered["called"] is None


def test_a_tool_that_does_not_match_its_pin_is_refused(tmp_path: Path):
    config = write_config(tmp_path, principal=True, pin="0" * 64)

    answered = drive(tmp_path, config)

    assert "echo" in answered["tools"], answered
    assert answered["called"]["is_error"] is True, answered["called"]
    assert "pinned digest" in answered["called"]["content"], answered["called"]
