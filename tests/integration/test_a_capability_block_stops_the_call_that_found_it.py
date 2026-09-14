"""A server whose upstream serves an undeclared tool serves no call in block or quarantine mode.

``enforcement_mode: block`` is meant to keep an upstream that serves tools
outside its declared ``expected_tools`` from being used. The check ran after the
start had kept the client, entered READY and recorded ``McpServerStarted``. Block
mode then marked the server DEAD, but closed nothing and stopped nothing, and the
invoke path never looked at the state again. So the call that started the server
invoked the tool it asked for, undeclared or not, and left the process running.
``quarantine``, documented to stop a server serving new requests, did not act
on drift at all. Now the start that finds the drift fails, and the server reads
``dead`` for a capability block: a later call is refused without starting it.

Both served paths are driven against ``tests/undeclared_tool_provider.py`` over
stdio. It records every request it receives and every process that runs it, so
"reaches no tool" means the upstream received no ``tools/call``, and "leaves no
process" means the OS lists none:

* ``hangar_call`` through the app ``serve --http`` serves, in
  ``_capability_block_harness.py``, in block, quarantine and alert mode;
* a flat call on ``front_door``, through the SDK's own ``ClientSession`` against
  ``mcp-hangar serve`` in a subprocess, in block and quarantine mode.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from tests._hangar_executable import hangar_executable

HARNESS = Path(__file__).with_name("_capability_block_harness.py")
PROVIDER = Path(__file__).resolve().parents[1] / "undeclared_tool_provider.py"
#: The enforcement modes that refuse a server whose tools drifted.
REFUSING = ("block", "quarantine")
MODES = (*REFUSING, "alert")

# As the harness names and counts them.
DECLARED, UNDECLARED, REPEATS = "add", "exfiltrate", 3
#: How a call to a server DEAD for a capability block is refused.
NOT_REVIVED = "capability block is not revived by a call"


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    # Concurrently, under the 60s pytest-timeout the integration job applies.
    tmp = tmp_path_factory.mktemp("capability-block")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


@pytest.mark.parametrize("mode", REFUSING)
def test_hangar_call_to_an_undeclared_tool_is_refused_every_time(runs: dict[str, dict[str, Any]], mode: str):
    run = runs[mode]

    assert len(run["undeclared"]) == REPEATS
    first, *later = run["undeclared"]
    # The call whose start found the drift, then calls refused without a start.
    assert first["success"] is False and "capability_violation" in first["error"], run
    for result in later:
        assert result["success"] is False, run
        assert NOT_REVIVED in result["error"].lower(), run
    assert run["tools_called"] == [], run


@pytest.mark.parametrize("mode", REFUSING)
def test_a_declared_tool_on_the_blocked_server_is_refused_too(runs: dict[str, dict[str, Any]], mode: str):
    run = runs[mode]

    assert run["declared"]["success"] is False, run
    assert NOT_REVIVED in run["declared"]["error"].lower(), run
    assert DECLARED not in run["tools_called"], run


@pytest.mark.parametrize("mode", REFUSING)
def test_the_blocked_server_leaves_no_process_and_is_not_restarted(runs: dict[str, dict[str, Any]], mode: str):
    run = runs[mode]

    # The first call's start is the only launch: no later call starts it again.
    assert len(run["launched"]) == 1, run
    assert run["running"] == [], run
    assert run["state"] == "dead", run
    assert "CapabilityViolationDetected" in run["events"], run
    assert "McpServerStarted" not in run["events"], run


def test_quarantine_records_the_quarantine_and_block_does_not(runs: dict[str, dict[str, Any]]):
    assert runs["quarantine"]["events"].count("McpServerCapabilityQuarantined") == 1, runs["quarantine"]
    assert "McpServerCapabilityQuarantined" not in runs["block"]["events"], runs["block"]


def test_alert_mode_still_serves_the_undeclared_tool(runs: dict[str, dict[str, Any]]):
    run = runs["alert"]

    assert [result["success"] for result in run["undeclared"]] == [True] * REPEATS, run
    assert run["declared"]["success"] is True, run
    assert run["tools_called"] == [UNDECLARED] * REPEATS + [DECLARED], run
    assert len(run["launched"]) == 1, run
    assert run["state"] == "ready", run
    assert run["events"].count("CapabilityViolationDetected") == 1, run
    assert "McpServerStarted" in run["events"], run
    assert "McpServerCapabilityQuarantined" not in run["events"], run


# The driver runs in its own process: the SDK's stdio client owns the lifetime of
# the gateway subprocess, and the upstream processes are that gateway's
# children, so they are asked about while the session is still open.
DRIVER = """\
import json, subprocess, sys, time

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BINARY, CONFIG, RECORD = sys.argv[1], sys.argv[2], sys.argv[3]
CALLS = [("exfiltrate", {"text": "hi"}), ("exfiltrate", {"text": "hi"}), ("add", {"a": 1, "b": 2})]


def recorded():
    try:
        with open(RECORD, encoding="utf-8") as record:
            return [json.loads(line) for line in record if line.strip()]
    except FileNotFoundError:
        return []


def running(pid):
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")


async def main() -> None:
    params = StdioServerParameters(command=BINARY, args=["--config", CONFIG, "serve"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # front_door starts every configured server at boot. Wait until that
            # start has listed the upstream's tools, then give the listing a
            # moment to carry them, so the calls meet whatever the start left.
            for _ in range(80):
                if any(entry.get("method") == "tools/list" for entry in recorded()):
                    break
                await anyio.sleep(0.25)
            for _ in range(12):
                if "exfiltrate" in {tool.name for tool in (await session.list_tools()).tools}:
                    break
                await anyio.sleep(0.25)

            answers = []
            for tool, arguments in CALLS:
                try:
                    dumped = (await session.call_tool(tool, arguments)).model_dump(mode="json")
                    is_error = bool(dumped.get("isError"))
                    answers.append({"tool": tool, "is_error": is_error, "content": dumped.get("content")})
                except Exception as exc:
                    answers.append({"tool": tool, "is_error": True, "content": type(exc).__name__ + ": " + str(exc)})

            # A closed upstream is gone within a second. An open one stays.
            launched = [entry["pid"] for entry in recorded() if entry["event"] == "start"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and any(running(pid) for pid in launched):
                await anyio.sleep(0.25)
            print(json.dumps({
                "answers": answers,
                "launched": launched,
                "running": [pid for pid in launched if running(pid)],
                "tools_called": [entry["tool"] for entry in recorded() if entry.get("method") == "tools/call"],
            }))


anyio.run(main)
"""

CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  drifting:
    mode: subprocess
    command: ["{python}", "{provider}"]
    env:
      UNDECLARED_PROVIDER_RECORD: "{record}"
    capabilities:
      tools:
        expected_tools: [add]
      enforcement_mode: {mode}
tool_access:
  mode: front_door
auth:
  stdio:
    principal:
      id: local-user
      tenant_id: local
      roles: [viewer]
"""


@pytest.mark.parametrize("mode", REFUSING)
def test_a_front_door_flat_call_is_refused_the_same_way(tmp_path: Path, mode: str):
    record = tmp_path / "upstream.jsonl"
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG.format(python=sys.executable, provider=PROVIDER, record=record, mode=mode))
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)

    result = subprocess.run(
        [sys.executable, str(driver), hangar_executable(), str(config), str(record)],
        capture_output=True,
        text=True,
        # Under the 60s pytest-timeout the CI job applies, so a hung gateway
        # fails with this test's own message and the client's output attached.
        timeout=50,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"the client failed:\n{result.stdout}\n{result.stderr}"
    answered = json.loads(result.stdout.strip().splitlines()[-1])

    assert [answer["is_error"] for answer in answered["answers"]] == [True, True, True], answered
    assert answered["tools_called"] == [], answered
    assert answered["running"] == [], answered
    # front_door's own start at boot is the only launch.
    assert len(answered["launched"]) == 1, answered
