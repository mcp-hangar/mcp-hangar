"""On the app ``serve --http`` serves, a tool's computed digest reads on hangar_tools and the REST tool routes (#1528).

``_tool_digest_harness.py`` runs the real ``bootstrap()`` in a fresh
interpreter, with API-key auth on and digest pins in the config file, and
serves the MCP app and the REST router as ``ServerLifecycle.run_http`` does.
It reads ``hangar_tools`` as two tenants, and ``GET /api/tools`` and
``GET /api/mcp_servers/{id}/tools`` as a fleet-wide viewer and as a viewer
granted only within a tenant.

What this pins:

- The digest on every surface equals what ``digest_tools`` -- the function
  ``mcp-hangar pin`` digests a server with -- computes for the same server.
- A pinned tool shows its pin next to it: the caller's tenant pin over the
  all-tenants one on ``hangar_tools``, the all-tenants one on REST. An
  unpinned tool has no ``pinned_digest``.
- A caller cannot read the digest or the pin of a tool it cannot list: not a
  tool its tenant is denied, not another tenant's pin, and nothing at all from
  a REST route its grant does not reach.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_tool_digest_harness.py")
SRC = Path(__file__).resolve().parents[2] / "src"

# As `_tool_digest_harness.py` names them.
TOOLS = ("read_item", "write_item", "secret_item", "plain_item")
PIN_READ_ALL = "e" * 64
PIN_WRITE_ALL = "b" * 64
PIN_WRITE_TENANT_A = "a" * 64
PIN_SECRET_ALL = "d" * 64
PIN_SECRET_TENANT_B = "c" * 64
REST = ("GET /api/tools", "GET /api/mcp_servers/{id}/tools")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    out = tmp_path_factory.mktemp("tool-digest") / "run.json"
    result = subprocess.run([sys.executable, str(HARNESS), str(out)], capture_output=True, text=True, timeout=55)
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    report: dict[str, Any] = json.loads(out.read_text())
    assert Path(report["hangar"]).is_relative_to(SRC), report["hangar"]
    assert sorted(report["pin_cli"]) == sorted(TOOLS), report["pin_cli"]
    return report


def _listed(run: dict[str, Any], tenant: str) -> dict[str, dict[str, Any]]:
    answer = run["hangar_tools"][tenant]
    assert answer["status"] == 200 and not answer["error"], answer
    return {tool["name"]: tool for tool in json.loads(answer["json"])["tools"]}


def _rest(run: dict[str, Any], route: str) -> dict[str, dict[str, Any]]:
    answer = run["rest"]["ops"][route]
    assert answer["status"] == 200, answer
    return {tool.get("tool_name", tool.get("name")): tool for tool in json.loads(answer["body"])["tools"]}


@pytest.mark.parametrize("tenant", ["tenant-a", "tenant-b"])
def test_hangar_tools_shows_the_digest_pin_computes(run: dict[str, Any], tenant: str) -> None:
    listed = _listed(run, tenant)

    assert {name: tool["digest"] for name, tool in listed.items()} == {name: run["pin_cli"][name] for name in listed}


@pytest.mark.parametrize("route", REST)
def test_rest_shows_the_digest_pin_computes(run: dict[str, Any], route: str) -> None:
    listed = _rest(run, route)

    assert {name: tool["digest"] for name, tool in listed.items()} == run["pin_cli"]


def test_hangar_tools_shows_the_pin_that_holds_the_callers_tenant(run: dict[str, Any]) -> None:
    pins = {
        tenant: {n: t.get("pinned_digest") for n, t in _listed(run, tenant).items()}
        for tenant in ("tenant-a", "tenant-b")
    }

    assert pins["tenant-a"] == {
        "read_item": PIN_READ_ALL,
        "write_item": PIN_WRITE_TENANT_A,
        "secret_item": PIN_SECRET_ALL,
        "plain_item": None,
    }
    assert pins["tenant-b"] == {"read_item": PIN_READ_ALL, "write_item": PIN_WRITE_ALL, "plain_item": None}


def test_a_drifted_pin_reads_next_to_what_is_served(run: dict[str, Any]) -> None:
    read_item = _listed(run, "tenant-a")["read_item"]

    assert read_item["pinned_digest"] == PIN_READ_ALL
    assert read_item["digest"] == run["pin_cli"]["read_item"] != PIN_READ_ALL


def test_an_unpinned_tool_has_no_pinned_digest(run: dict[str, Any]) -> None:
    surfaces = [_listed(run, "tenant-a"), _listed(run, "tenant-b"), *(_rest(run, route) for route in REST)]

    assert all("pinned_digest" not in surface["plain_item"] for surface in surfaces)


@pytest.mark.parametrize("route", REST)
def test_rest_shows_the_all_tenants_pin(run: dict[str, Any], route: str) -> None:
    listed = _rest(run, route)

    assert {name: tool.get("pinned_digest") for name, tool in listed.items()} == {
        "read_item": PIN_READ_ALL,
        "write_item": PIN_WRITE_ALL,
        "secret_item": PIN_SECRET_ALL,
        "plain_item": None,
    }
    assert PIN_WRITE_TENANT_A not in run["rest"]["ops"][route]["body"]
    assert PIN_SECRET_TENANT_B not in run["rest"]["ops"][route]["body"]


def test_a_tool_the_tenant_cannot_list_has_no_digest_or_pin_in_its_answer(run: dict[str, Any]) -> None:
    body = run["hangar_tools"]["tenant-b"]["body"]

    assert sorted(_listed(run, "tenant-b")) == ["plain_item", "read_item", "write_item"]
    assert "secret_item" not in body
    assert run["pin_cli"]["secret_item"] not in body
    assert PIN_SECRET_ALL not in body
    assert PIN_SECRET_TENANT_B not in body


def test_another_tenants_pin_is_not_in_the_answer(run: dict[str, Any]) -> None:
    assert PIN_WRITE_TENANT_A not in run["hangar_tools"]["tenant-b"]["body"]
    assert PIN_SECRET_TENANT_B not in run["hangar_tools"]["tenant-a"]["body"]


@pytest.mark.parametrize("route", REST)
def test_a_grant_within_one_tenant_reads_no_digest_on_rest(run: dict[str, Any], route: str) -> None:
    answer = run["rest"]["scoped"][route]

    assert answer["status"] == 403
    assert not [digest for digest in run["pin_cli"].values() if digest in answer["body"]]
