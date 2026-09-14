"""A remote server and discovery built with `HangarConfig` take effect (#1423).

The builder wrote a remote server's address as `url` and discovery as
`discovery.docker`/`.kubernetes`/`.filesystem`. The gateway reads `endpoint`
and `discovery: {enabled, sources}`, so the server booted with no address and
discovery never turned on, and until #1415 nothing said so. The facade also
never started the discovery bootstrap built, so the section would not have run
even with the right keys.

Each mode runs ``_builder_served_harness.py`` in a fresh interpreter under
``HANGAR_CONFIG_STRICT=1``: ``SyncHangar.from_builder`` boots the real
``bootstrap()``. The remote server then answers a ``hangar_call`` through the
app ``serve --http`` serves, against a stub HTTP MCP upstream. Discovery is
checked by the sources the orchestrator holds and the registry lists, which
needs neither Docker nor a cluster to be reachable. The unit-level check that
every builder option passes the schema is in ``tests/unit/test_facade.py``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_builder_served_harness.py")
MODES = ("remote", "discovery", "filesystem")


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=50,
        env={**os.environ, "HANGAR_CONFIG_STRICT": "1"},
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("builder")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


def test_a_builder_remote_server_boots_with_its_address(runs) -> None:
    remote = runs["remote"]

    assert remote["spec"]["endpoint"] == remote["address"]
    assert "url" not in remote["spec"]


def test_it_answers_a_call_on_the_served_path(runs) -> None:
    (result,) = runs["remote"]["batch"]["results"]

    assert result["success"] is True, result
    assert "did read_item" in json.dumps(result)


def test_the_call_reached_the_upstream(runs) -> None:
    # Once through `hangar_call`, once through `SyncHangar.invoke`.
    assert runs["remote"]["upstream_called"] == ["read_item", "read_item"]
    assert "did read_item" in json.dumps(runs["remote"]["invoked"])


def test_discovery_is_enabled_with_the_requested_sources(runs) -> None:
    discovery = runs["discovery"]
    directory = discovery["directory"]

    assert discovery["config"] == {
        "enabled": True,
        "sources": [
            {"type": "docker", "mode": "additive"},
            {"type": "kubernetes", "mode": "additive"},
            {"type": "filesystem", "mode": "additive", "path": directory},
        ],
    }
    assert discovery["held"] == ["docker", "filesystem", "kubernetes"]
    assert discovery["registered"] == [
        ["docker", "additive", {}],
        ["filesystem", "additive", {"path": directory}],
        ["kubernetes", "additive", {}],
    ]


def test_the_facade_runs_the_discovery_it_built(runs) -> None:
    assert runs["discovery"]["running"] is True


def test_the_kubernetes_entry_reaches_the_kubernetes_factory(runs) -> None:
    # None when the `kubernetes` extra is installed: then the real source is
    # the one `held` lists above.
    received = runs["discovery"]["kubernetes_stand_in"]

    assert received in (None, [{"type": "kubernetes", "mode": "additive"}])


def test_the_facade_stops_the_discovery_it_started(runs) -> None:
    filesystem = runs["filesystem"]

    assert filesystem["started"] == {"running": True, "thread": True}
    assert filesystem["stopped"] == {"running": False, "thread": False}
