"""A SIEM export the gateway cannot perform refuses startup; one that breaks later is seen (#1701).

An unknown ``MCP_COMPLIANCE_FORMAT`` (``cefx``) logged ``unknown_compliance_format``
at warning and the gateway served calls with no export. An output whose
directory did not exist logged one error per record and dropped every one. An
operator who configured SIEM export ran with none.

Each case runs ``_compliance_export_harness.py`` in a fresh interpreter: the
real ``bootstrap()``, and for the runtime case real ``hangar_call``s through the
served app. A boot that should be refused must exit non-zero, naming the value.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.security

HARNESS = Path(__file__).with_name("_compliance_export_harness.py")


def _run(mode: str, tmp: Path, fmt: str, output: Path | None) -> tuple[subprocess.CompletedProcess[str], Path]:
    out = tmp / "run.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MCP_COMPLIANCE_", "OTEL_"))}
    env["MCP_COMPLIANCE_FORMAT"] = fmt
    if output is not None:
        env["MCP_COMPLIANCE_OUTPUT"] = str(output)
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)], capture_output=True, text=True, timeout=50, env=env
    )
    return result, out


def _refused(result: subprocess.CompletedProcess[str], out: Path) -> str:
    assert result.returncode != 0 and not out.exists(), f"booted:\n{result.stderr[-3000:]}"
    assert "ConfigurationError" in result.stderr
    return result.stderr


def test_an_unknown_format_refuses_boot(tmp_path):
    stderr = _refused(*_run("boot", tmp_path, "cefx", tmp_path / "feed.log"))

    assert "Unknown MCP_COMPLIANCE_FORMAT 'cefx'" in stderr
    assert "cef, json-lines, jsonlines, leef, syslog" in stderr


def test_an_output_in_a_missing_directory_refuses_boot(tmp_path):
    output = tmp_path / "missing" / "feed.log"
    stderr = _refused(*_run("boot", tmp_path, "cef", output))

    assert f"MCP_COMPLIANCE_OUTPUT '{output}' cannot be appended to" in stderr


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes through a read-only mode")
def test_an_output_in_a_read_only_directory_refuses_boot(tmp_path):
    feed_dir = tmp_path / "ro"
    feed_dir.mkdir(mode=0o500)
    try:
        stderr = _refused(*_run("boot", tmp_path, "jsonlines", feed_dir / "feed.jsonl"))
    finally:
        feed_dir.chmod(0o700)

    assert "cannot be appended to" in stderr and "Permission denied" in stderr


def test_a_valid_feed_boots_and_the_format_is_read_trimmed(tmp_path):
    result, out = _run("boot", tmp_path, " CEF ", tmp_path / "feed.log")

    assert result.returncode == 0 and json.loads(out.read_text()) == {"booted": True}, result.stderr[-3000:]


@pytest.fixture(scope="module")
def runtime(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp = tmp_path_factory.mktemp("compliance_runtime")
    feed = tmp / "feed" / "audit.jsonl"
    feed.parent.mkdir()
    result, out = _run("runtime", tmp, "jsonlines", feed)
    assert result.returncode == 0 and out.exists(), result.stderr[-4000:]
    return {**json.loads(out.read_text()), "stderr": result.stderr, "feed": feed.read_text().splitlines()}


def test_the_calls_are_served_throughout(runtime):
    # Export failing does not refuse calls: that mode is out of scope.
    assert [runtime[p]["calls"] for p in ("writable", "removed", "restored")] == [[True], [True, True], [True]]


def test_a_failed_write_is_counted_by_format_and_reason(runtime):
    assert runtime["writable"]["counter"] == {}
    dropped = runtime["removed"]["counter"]
    assert set(dropped) == {"jsonlines/not_found"} and dropped["jsonlines/not_found"] >= 2
    assert runtime["restored"]["counter"] == dropped


def test_a_failing_feed_shows_in_health_and_readiness_stays_up(runtime):
    removed = runtime["removed"]
    assert removed["health"]["status"] == "degraded"
    assert removed["health"]["compliance_export"]["status"] == "degraded"
    assert removed["health"]["compliance_export"]["last_reason"] == "not_found"
    assert removed["health"]["compliance_export"]["output"].endswith("audit.jsonl")
    # Readiness reports it without the path, and does not drain the replica.
    assert removed["ready_code"] == 200
    assert removed["ready"]["compliance_export"]["status"] == "degraded"
    assert "output" not in removed["ready"]["compliance_export"]

    assert runtime["writable"]["health"]["compliance_export"]["status"] == "healthy"
    assert runtime["restored"]["health"]["status"] == "healthy"
    assert runtime["restored"]["ready"]["compliance_export"]["status"] == "healthy"


def test_a_failure_burst_is_logged_once_and_its_recovery_once(runtime):
    assert runtime["stderr"].count("compliance_export_write_failed") == 1
    assert runtime["stderr"].count("compliance_export_write_recovered") == 1
    # The restored directory holds the record written after it came back.
    assert [json.loads(line).get("tool_name") for line in runtime["feed"] if "tool_name" in line] == ["add"]
