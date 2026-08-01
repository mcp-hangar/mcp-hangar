#!/usr/bin/env python3
"""Smoke the *artifact*, not the repo tree — gate D of the release matrix (#550).

Everything else in CI tests the working copy. That is exactly the blind spot
that shipped #561: the code was fine, the *packaging* was not (an uncapped
`mcp` pin, so a clean install resolved the SDK into a major whose server
surface this line does not use and the gateway died at import). No test that
imports from `src/` can see that class of defect, because it never resolves
dependencies the way a user's `pip install` does.

So this builds a throwaway virtualenv, installs mcp-hangar into it *the way a
user would*, and drives the result end to end:

    venv -> install -> stub upstream -> serve --http -> /health/live
         -> a real tools/call through the gateway -> /metrics

Two placements, deliberately different guarantees:

* ``--wheel dist/*.whl`` runs BEFORE publish. Dependencies still resolve from
  the index, so it catches #561 while the wheel can still be stopped.
* ``--version 2.0.0rc1`` runs AFTER publish and installs from the index. It
  verifies precisely what users receive, but by then the wheel is immutable —
  this one reports, it cannot block.

Run both, in that order.

Deliberately stdlib-only and never imports mcp_hangar in this process: the
whole point is that the installed distribution, not this checkout, is under
test. The MCP client work happens in a child process running the venv's python
(the SDK arrives as a dependency of the thing we just installed).

Usage:
    python scripts/smoke_published_artifact.py --wheel dist/mcp_hangar-*.whl
    python scripts/smoke_published_artifact.py --version 2.0.0rc1
"""

from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# The gateway must answer /health/live within this budget. Generous: a cold venv
# on a loaded CI runner is slower than a laptop, and a false red here would get
# the gate switched off, which is worse than a slow gate.
STARTUP_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 0.5

# A freshly published version is not visible to every PyPI CDN node at once, and
# the *simple index* pip reads lags the JSON API the release notes link to. A
# retry has always been here, but the budget was six flat 10s waits -- 53 seconds
# end to end -- and the 2.0.1 release fell outside it: all six attempts expired,
# the job went red, and a re-run minutes later passed with nothing changed
# (#680). `--no-cache-dir` is not the fix; the staleness is upstream, not local.
# This budget spans ~3.5 minutes (5+10+20+40+45+45+45).
INSTALL_ATTEMPTS = 8
INSTALL_BACKOFF_S = 5.0
INSTALL_BACKOFF_MAX_S = 45.0

# Independent evidence that a version exists, used to tell propagation lag apart
# from a version that was never published. The JSON API is served from different
# infrastructure than the simple index and was already returning 200 for 2.0.1
# while pip still could not see it.
PYPI_RELEASE_JSON = "https://pypi.org/pypi/{project}/{version}/json"
PROJECT = "mcp-hangar"

# A stdio MCP upstream for the gateway to proxy. It is written here, at smoke
# time, rather than shipped in the wheel: `examples/` is not packaged, so a
# clean venv has no backend to point at. The SDK it imports arrives as a
# dependency of mcp-hangar itself, which keeps this test free of any install
# the artifact did not already pull in.
STUB_SERVER = '''\
"""Throwaway stdio MCP upstream for the published-artifact smoke."""

try:  # SDK v2
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # SDK v1
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("smoke-upstream")


@mcp.tool(name="add")
def add(a: float, b: float) -> dict:
    """Add two numbers."""
    return {"result": a + b}


if __name__ == "__main__":
    mcp.run()
'''

CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  smoke:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
"""

# Driver for the one call that matters. Run with the venv's python so the MCP
# client comes from the installed dependency set. Prints a single JSON line so
# the parent can assert on it without scraping logs.
DRIVER = '''\
import anyio, json, sys

BASE = sys.argv[1]

try:  # SDK v2 renamed the factory and moved headers onto an httpx client
    from mcp.client.streamable_http import streamable_http_client as factory
    V2 = True
except ImportError:  # SDK v1
    from mcp.client.streamable_http import streamablehttp_client as factory
    V2 = False

from mcp import ClientSession


def answer_of(dumped):
    """Dig the upstream's own number out of the batch envelope.

    hangar_call wraps the backend reply twice -- batch envelope, then the
    upstream's `content[].text` holding its JSON. Walking for the innermost
    `result` is version-tolerant; string-matching the serialized form is not
    (the tool returns 5.0, not 5).
    """
    def walk(node, depth=0):
        if depth > 8:
            return None
        if isinstance(node, dict):
            if isinstance(node.get("result"), (int, float)):
                return float(node["result"])
            for value in node.values():
                found = walk(value, depth + 1)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item, depth + 1)
                if found is not None:
                    return found
        elif isinstance(node, str) and node.lstrip().startswith("{"):
            try:
                return walk(json.loads(node), depth + 1)
            except ValueError:
                return None
        return None

    return walk(dumped)


async def main() -> None:
    async with factory(f"{BASE}/mcp") as streams:
        read, write = tuple(streams)[:2]
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            result = await session.call_tool(
                "hangar_call",
                {"calls": [{"mcp_server": "smoke", "tool": "add", "arguments": {"a": 2, "b": 3}}]},
            )
            dumped = result.model_dump(mode="json")
            print(json.dumps({
                "sdk_v2": V2,
                "tools": tools,
                "answer": answer_of(dumped),
                "payload": json.dumps(dumped, default=str),
            }))


anyio.run(main)
'''


def log(message: str) -> None:
    print(f"  {message}", flush=True)


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 -- loopback, or pypi.org over TLS
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return 0, ""


def make_venv(root: Path) -> tuple[Path, Path]:
    """Create the venv and return (python, mcp-hangar binary) paths."""
    venv_dir = root / "venv"
    run([sys.executable, "-m", "venv", str(venv_dir)]).check_returncode()
    bin_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
    python = bin_dir / ("python.exe" if sys.platform == "win32" else "python")
    run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    return python, bin_dir / ("mcp-hangar.exe" if sys.platform == "win32" else "mcp-hangar")


class IndexLag(Exception):
    """The version is provably published, but the simple index has not served it yet.

    Raised only with positive evidence from PyPI's JSON API. Kept separate from
    every other install failure because the two mean opposite things: this one
    says nothing at all about the artifact and resolves itself in minutes, while
    a wheel that will not install is a defect that shipped.
    """


def version_is_published(version: str, index_url: str | None) -> bool | None:
    """Ask PyPI's JSON API whether this version exists. None when unanswerable."""
    if index_url:
        return None  # a custom index has no JSON API we can assume the shape of
    status, _ = http_get(PYPI_RELEASE_JSON.format(project=PROJECT, version=version), timeout=10.0)
    if status == 200:
        return True
    if status == 404:
        return False
    return None  # network trouble or a 5xx: no evidence either way


def backoff_for(attempt: int) -> float:
    return min(INSTALL_BACKOFF_S * 2 ** (attempt - 1), INSTALL_BACKOFF_MAX_S)


def install(python: Path, *, wheel: str | None, version: str | None, index_url: str | None) -> None:
    """Install the artifact under test, retrying only the index path."""
    if wheel:
        matches = sorted(Path().glob(wheel)) if any(ch in wheel for ch in "*?[") else [Path(wheel)]
        if not matches:
            raise SystemExit(f"FAIL: no wheel matches {wheel!r}")
        target = str(matches[-1])
        log(f"installing {target} (dependencies still resolve from the index)")
        result = run([str(python), "-m", "pip", "install", target])
        if result.returncode != 0:
            raise SystemExit(f"FAIL: install of the built wheel failed\n{result.stdout}\n{result.stderr}")
        return

    spec = f"mcp-hangar=={version}"
    # NOT `--pre`. An exact `==` pin already admits a prerelease of *this*
    # project, while `--pre` opts the entire resolve into prereleases of every
    # dependency -- which is how the v2 preview install ended up pulling
    # httpx 1.0.dev3 and dying on `httpx.AsyncClient`. That resolve is a real
    # hazard, so it gets tested too (see `--also-pre`), but it is not the
    # supported form and must not be what this gate asserts.
    cmd = [str(python), "-m", "pip", "install", spec]
    if index_url:
        cmd[4:4] = ["--index-url", index_url]
    last = None
    for attempt in range(1, INSTALL_ATTEMPTS + 1):
        log(f"installing {spec} from the index (attempt {attempt}/{INSTALL_ATTEMPTS})")
        last = result = run(cmd)
        if result.returncode == 0:
            return
        # Distinguish "not visible yet" from "genuinely broken": only the former
        # is worth waiting on, and pretending otherwise hides real failures.
        absent = "No matching distribution" in result.stderr or "could not find a version" in result.stderr.lower()
        if not absent:
            # pip found the version and the install still failed -- a bad
            # dependency pin, an unbuildable sdist, a broken wheel. Waiting
            # cannot change this, and it is exactly what the gate exists for.
            raise SystemExit(
                f"FAIL: the wheel will not install.\n"
                f"  pip located {spec} on the index; the install itself failed. This is a defect in the\n"
                f"  published artifact, not a timing problem, and re-running will not change it.\n"
                f"{result.stdout}\n{result.stderr}"
            )
        if attempt < INSTALL_ATTEMPTS:
            delay = backoff_for(attempt)
            log(f"{spec} is not on the simple index yet; retrying in {delay:.0f}s")
            time.sleep(delay)

    # The budget is spent and pip still cannot see the version. Whether that is a
    # release that failed to publish or an index that has not caught up is not a
    # guess -- the JSON API answers it.
    stdout = last.stdout if last else ""
    stderr = last.stderr if last else ""
    spent = sum(backoff_for(i) for i in range(1, INSTALL_ATTEMPTS))
    published = version_is_published(str(version), index_url)
    if published:
        raise IndexLag(
            f"{spec} is published -- PyPI's JSON API serves it -- but the simple index pip reads has\n"
            f"  not caught up after {INSTALL_ATTEMPTS} attempts over {spent:.0f}s. Nothing is known to be wrong\n"
            f"  with the artifact; this is index propagation and clears on its own within minutes."
        )
    detail = (
        "PyPI's JSON API does not serve it either (404): the version was never published."
        if published is False
        else "PyPI's JSON API could not be reached, so propagation cannot be ruled in or out."
    )
    raise SystemExit(
        f"FAIL: {spec} is not installable from the index after {INSTALL_ATTEMPTS} attempts over {spent:.0f}s.\n"
        f"  {detail}\n{stdout}\n{stderr}"
    )


def assert_isolated(python: Path, workdir: Path, expected_version: str | None) -> str:
    """The installed distribution must be what runs — not this checkout.

    Cheap, but it is the assumption the whole gate rests on: run from a
    directory that is not the repo and confirm the import resolves inside the
    venv. Without this the smoke could quietly exercise `src/` and pass while
    the wheel is broken.
    """
    probe = (
        "import mcp_hangar, json; print(json.dumps({'file': mcp_hangar.__file__, 'version': mcp_hangar.__version__}))"
    )
    result = run([str(python), "-c", probe], cwd=str(workdir))
    if result.returncode != 0:
        raise SystemExit(f"FAIL: the installed package does not import\n{result.stdout}\n{result.stderr}")
    info = json.loads(result.stdout.strip().splitlines()[-1])

    if str(python.parent.parent) not in info["file"]:
        raise SystemExit(
            f"FAIL: mcp_hangar resolved outside the venv ({info['file']}) — the tree is under test, not the artifact"
        )
    if expected_version and info["version"] != expected_version:
        raise SystemExit(f"FAIL: installed {info['version']}, expected {expected_version}")

    log(f"import resolves to the artifact: {info['version']} at {info['file']}")
    return str(info["version"])


def serve(binary: Path, python: Path, workdir: Path) -> tuple[subprocess.Popen, str]:
    (workdir / "stub_server.py").write_text(STUB_SERVER)
    (workdir / "config.yaml").write_text(CONFIG.format(python=str(python), server=str(workdir / "stub_server.py")))

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            str(binary),
            "--config",
            str(workdir / "config.yaml"),
            "serve",
            "--http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(workdir),
    )

    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.communicate()[0] or ""
            raise SystemExit(f"FAIL: the gateway exited before becoming healthy (rc={proc.returncode})\n{out[-3000:]}")
        if http_get(f"{base_url}/health/live", timeout=2.0)[0] == 200:
            log(f"/health/live answered 200 on {base_url}")
            return proc, base_url
        time.sleep(POLL_INTERVAL_S)

    proc.terminate()
    out = ""
    try:
        out = proc.communicate(timeout=5)[0] or ""
    except subprocess.TimeoutExpired:
        proc.kill()
    raise SystemExit(f"FAIL: no /health/live within {STARTUP_TIMEOUT_S}s\n{out[-3000:]}")


def drive_a_real_call(python: Path, workdir: Path, base_url: str) -> None:
    (workdir / "driver.py").write_text(DRIVER)
    result = run([str(python), str(workdir / "driver.py"), base_url], cwd=str(workdir), timeout=120)
    if result.returncode != 0:
        raise SystemExit(f"FAIL: the tool call did not complete\n{result.stdout}\n{result.stderr}")

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if "hangar_call" not in payload["tools"]:
        raise SystemExit(f"FAIL: the gateway served no hangar_call; tools={payload['tools']}")
    # Assert the upstream actually ran. A gateway that answers but never reaches
    # its backend would otherwise pass every check above.
    if payload["answer"] != 5.0:
        raise SystemExit(f"FAIL: 2 + 3 came back as {payload['answer']!r}, not 5\n{payload['payload'][:1500]}")

    log(f"tools/call round-tripped through a cold backend (SDK v{'2' if payload['sdk_v2'] else '1'})")


def assert_metrics(base_url: str) -> None:
    status, body = http_get(f"{base_url}/metrics", timeout=10.0)
    if status != 200:
        raise SystemExit(f"FAIL: /metrics answered {status}")
    # #550 asks for `gen_ai.tool.name` here. That name is a *span* attribute, not
    # a Prometheus series -- asserting it would fail forever for the wrong
    # reason. The metric that proves the invocation path was exercised is
    # mcp_hangar_tool_calls_total, so that is what is checked.
    required = ("mcp_hangar_tool_calls_total", "mcp_hangar_")
    missing = [name for name in required if name not in body]
    if missing:
        raise SystemExit(f"FAIL: /metrics is missing {missing}")
    log("/metrics carries mcp_hangar_tool_calls_total for the call just made")


def report_pre_resolve(root: Path, version: str, index_url: str | None) -> None:
    """Report what `pip install --pre` drags in — advisory, never fatal.

    `--pre` opts the whole resolve into prereleases, so an unrelated upstream
    alpha can break the install without anything here changing. That is worth
    knowing about and wrong to gate on: a red build caused by someone else's
    dev release is not a defect in this artifact, and a gate that goes red for
    reasons the team cannot fix gets switched off.

    It is reported because it is not hypothetical: this resolve pulled
    httpx 1.0.dev3 and the gateway died on `httpx.AsyncClient` -- while the
    documented `pip install --pre mcp-hangar` told users to do exactly that.
    """
    print("\nAdvisory: resolving with --pre (not the supported form)", flush=True)
    venv_dir = root / "prevenv"
    if run([sys.executable, "-m", "venv", str(venv_dir)]).returncode != 0:
        log("could not create the advisory venv; skipping")
        return
    bin_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
    python = bin_dir / ("python.exe" if sys.platform == "win32" else "python")

    cmd = [str(python), "-m", "pip", "install", "--pre", f"mcp-hangar=={version}"]
    if index_url:
        cmd[4:4] = ["--index-url", index_url]
    if run(cmd).returncode != 0:
        log("WARNING: `pip install --pre` does not even install")
        return

    listed = run([str(python), "-m", "pip", "list", "--format=json"])
    prereleases = []
    if listed.returncode == 0:
        import re

        pattern = re.compile(r"(a|b|rc|\.dev)\d", re.IGNORECASE)
        prereleases = [
            f"{pkg['name']}=={pkg['version']}"
            for pkg in json.loads(listed.stdout)
            if pattern.search(pkg["version"]) and pkg["name"] not in ("mcp-hangar", "mcp", "mcp-types")
        ]

    # Import is not the bar. The httpx 1.0.dev3 break imports fine and only
    # surfaces when the gateway starts, so the advisory runs the same serve +
    # tool-call path as the gate -- otherwise it would have reported this
    # resolve as healthy.
    binary = python.parent / ("mcp-hangar.exe" if sys.platform == "win32" else "mcp-hangar")
    prework = root / "prework"
    prework.mkdir(exist_ok=True)
    proc = None
    try:
        proc, base_url = serve(binary, python, prework)
        drive_a_real_call(python, prework, base_url)
        log(f"--pre resolve serves a real call; upstream prereleases pulled in: {prereleases or 'none'}")
    except SystemExit as failure:
        first_line = str(failure).splitlines()[0] if str(failure) else "?"
        log(f"WARNING: the --pre resolve does not work: {first_line}")
        log(f"WARNING: upstream prereleases pulled in: {prereleases or 'none'}")
        log("WARNING: do not advertise a bare `pip install --pre` while this is true; document the exact pin instead")
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wheel", help="path or glob to a built wheel (pre-publish gate)")
    source.add_argument("--version", help="published version to install from the index (post-publish check)")
    parser.add_argument("--index-url", help="override the package index (used with --version)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory for debugging")
    parser.add_argument(
        "--also-pre",
        action="store_true",
        help="additionally resolve with `pip install --pre` and report the outcome (advisory, never fails the run)",
    )
    parser.add_argument(
        "--tolerate-index-lag",
        action="store_true",
        help=(
            "warn instead of failing when the version is provably published but the simple index has not "
            "served it within the retry budget (post-publish placement only)"
        ),
    )
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="hangar-smoke-"))
    workdir = root / "work"
    workdir.mkdir()
    proc = None
    try:
        print(f"Smoking {'wheel ' + args.wheel if args.wheel else 'published ' + args.version} in {root}", flush=True)
        python, binary = make_venv(root)
        try:
            install(python, wheel=args.wheel, version=args.version, index_url=args.index_url)
        except IndexLag as lag:
            # A red here is not free. This job cannot stop the release -- the
            # wheel is immutable by the time it runs -- so its only effect is on
            # the reader, and a failure that fires for reasons unrelated to the
            # artifact teaches them to re-run it without looking. That is how a
            # real packaging failure gets waved through (#680). So: loud, but not
            # red, and only ever on positive proof the version is published.
            if not args.tolerate_index_lag:
                raise SystemExit(f"FAIL: {lag}") from lag
            print(f"::warning title=PyPI index lag::{lag}", flush=True)
            print(f"\nSKIPPED — {lag}", flush=True)
            return 0
        assert_isolated(python, workdir, args.version)
        proc, base_url = serve(binary, python, workdir)
        drive_a_real_call(python, workdir, base_url)
        assert_metrics(base_url)
        print("\nPASS — the installed artifact serves a real tool call end to end.", flush=True)
        if args.also_pre and args.version:
            report_pre_resolve(root, args.version, args.index_url)
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if args.keep:
            print(f"kept: {root}", flush=True)
        else:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
