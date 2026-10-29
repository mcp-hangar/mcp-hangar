"""What a caller is shown is what it may call, on every surface (#1785).

Every path-divergence defect so far was a listing and a call, or two call
paths, disagreeing for the same caller (#1763). This is the mechanical check: a
Hypothesis property over generated governance -- tool policies on servers,
groups and members, tenant-scoped policies, withdrawals of every kind, prompt
and resource ``access:`` policies, canary pins and block-mode digest pins --
asserted through the app ``bootstrap()`` composes, per caller, per surface.

Each topology runs ``_listing_call_parity_harness.py`` in a fresh interpreter,
for the reason ``test_route_decisions_on_the_served_app.py`` gives. Its
docstring states the invariant as the tracker review on #1785 narrowed it, and
how a refusal is classed. One test per surface reads the harness's report, so a
failure names the surface, the shrunk governance and every broken assertion.

The example budget comes from ``HANGAR_PARITY_EXAMPLES``: a small one by
default, a large one in the nightly run (``live-verify.yml``). Two seeds run
first on every budget, the configurations the pre-#1784 surfaces got wrong;
each surface switched back to its pre-#1784 scope assembly fails on them (the
mutation runs are recorded in the PR that closed #1785).

Naming: neutral placeholders only (pool, alpha, beta, gamma, tenant-*).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.integration._listing_call_parity_harness import EGRESS, FRONT_DOOR, SEEDS, SURFACES

HARNESS = Path(__file__).with_name("_listing_call_parity_harness.py")
#: The default tier's budget: the seeds plus this many generated examples per surface.
DEFAULT_EXAMPLES = "5"

#: Known divergences, each with the issue that owns it. strict: the day one is
#: fixed, its test passes, and the xfail must go.
KNOWN = {
    "hangar_details_group": (
        "#2019: hangar_details on a group returns group.to_status_dict() unfiltered, so a pinned tenant "
        "is shown every member, not only the one its calls are routed to"
    ),
}


def _cases() -> list[Any]:
    return [
        pytest.param(
            topology,
            surface,
            id=f"{topology}-{surface}",
            marks=[pytest.mark.xfail(strict=True, reason=KNOWN[surface])] if surface in KNOWN else [],
        )
        for topology in (EGRESS, FRONT_DOOR)
        for surface in SURFACES[topology]
    ]


_REPORTS: dict[str, dict[str, Any]] = {}


def _report(topology: str, workdir: Path) -> dict[str, Any]:
    """The harness's report for *topology*, run once per module."""
    if topology not in _REPORTS:
        out = workdir / "report.json"
        env = {**os.environ, "HANGAR_PARITY_EXAMPLES": os.environ.get("HANGAR_PARITY_EXAMPLES", DEFAULT_EXAMPLES)}
        result = subprocess.run(
            [sys.executable, str(HARNESS), topology, str(out)],
            capture_output=True,
            text=True,
            env=env,
            timeout=3600,
        )
        assert result.returncode == 0 and out.exists(), (
            f"{topology}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
        )
        report = json.loads(out.read_text())
        # The harness must have imported this checkout, not an installed copy.
        assert report["hangar"].startswith(str(Path(__file__).resolve().parents[2])), report["hangar"]
        _REPORTS[topology] = report
    return _REPORTS[topology]


@pytest.fixture(scope="module")
def reports(tmp_path_factory: pytest.TempPathFactory) -> Any:
    return lambda topology: _report(topology, tmp_path_factory.mktemp(f"parity_{topology}"))


@pytest.mark.parametrize(("topology", "surface"), _cases())
def test_what_the_surface_shows_a_caller_is_what_it_may_call(reports: Any, topology: str, surface: str) -> None:
    run = reports(topology)["surfaces"][surface]

    assert run["passed"], f"{surface} ({topology}) broke the invariant on\n  {run['falsifying']}\n" + "\n".join(
        f"  - {problem}" for problem in run["problems"]
    )


@pytest.mark.parametrize("topology", [EGRESS, FRONT_DOOR])
def test_the_run_was_not_vacuous(reports: Any, topology: str) -> None:
    """Every passing surface saw the seeds and the budget, and most examples governed something."""
    report = reports(topology)
    for surface, run in report["surfaces"].items():
        if not run["passed"]:
            continue
        assert run["examples"] >= len(SEEDS) + 1, (surface, run)
        assert run["governed"] * 2 >= run["examples"], (surface, run)
