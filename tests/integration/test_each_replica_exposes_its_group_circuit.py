"""A group's circuit is on ``/metrics`` and follows the served path (#1357).

Each mode runs ``_group_recovery_harness.py`` in a fresh interpreter: the real
``bootstrap()``, ``hangar_call`` through the app ``serve --http`` serves, and
the health worker ``bootstrap()`` created. It scrapes the endpoint ``serve
--http`` mounts at ``/metrics`` right after bootstrap and after every call that
can move the circuit. Nothing writes the gauge by hand. The circuit opens
through member failures on real calls and closes when a passing health check
brings a member back.

The scrape is the point, not a formality. Five metrics were once defined,
written to and documented with ready-made PromQL, and never registered. Every
query against them returned nothing, on every deployment (#1059). So the PromQL
the guides document, in ``tests/_promql.py``, runs here too, against two real
scrapes labelled as two replicas, the way Prometheus stores them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tests._promql import GROUP_CIRCUIT_QUERIES, Labels, Scrape, Vector, evaluate, parse_scrape

HARNESS = Path(__file__).with_name("_group_recovery_harness.py")
MODES = ("single", "pair")

# As `_group_recovery_harness.py` names them.
GROUP, METRIC = "math-pool", "mcp_hangar_group_circuit_open"
TYPE_LINE = f"# TYPE {METRIC} gauge"


def _sample(value: float) -> str:
    return f'{METRIC}{{group="{GROUP}"}} {value}'


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
    tmp = tmp_path_factory.mktemp("group-circuit-metric")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


@pytest.mark.parametrize("mode", MODES)
def test_a_loaded_group_is_on_a_real_scrape_before_any_call(runs, mode):
    """Seeded at bootstrap, labelled by the group alone: no tenant, no member."""
    assert runs[mode]["metric"]["boot"] == [TYPE_LINE, _sample(0.0)]


@pytest.mark.parametrize("mode", MODES)
def test_after_every_call_the_gauge_says_what_hangar_group_list_says(runs, mode):
    steps = runs[mode]["metric"]["failures"]

    assert [step["gauge"] for step in steps] == [1.0 if step["circuit_open"] else 0.0 for step in steps], steps


def test_member_failures_on_real_calls_open_it(runs):
    metric = runs["single"]["metric"]

    assert metric["before"] == 0.0
    assert [step["gauge"] for step in metric["failures"]] == [0.0, 1.0]
    assert metric["tripped"] == 1.0


def test_it_changes_on_every_transition_not_once(runs):
    """math-a's failures open it, math-b's start closes it, math-b's failures open it again."""
    metric = runs["pair"]["metric"]

    assert [step["gauge"] for step in metric["failures"]] == [0.0, 1.0, 0.0, 1.0]
    assert metric["tripped"] == 1.0


@pytest.mark.parametrize("mode", MODES)
def test_a_member_back_in_rotation_closes_it(runs, mode):
    run = runs[mode]

    assert run["status"]["after"]["circuit_open"] is False, run["status"]["after"]
    assert run["metric"]["after"] == 0.0


def test_a_deleted_group_leaves_the_scrape_and_a_created_one_joins_it(runs):
    metric = runs["single"]["metric"]

    assert metric["deleted"] == [TYPE_LINE]
    assert metric["created"] == [TYPE_LINE, _sample(0.0)]


# ----------------------------------------------------------------------------
# The documented PromQL, over two replicas.
# ----------------------------------------------------------------------------


def _fleet(replicas: dict[str, str], at: float) -> Scrape:
    """Several replicas' scrapes as Prometheus stores them: every series gains its ``instance``."""
    samples: dict[str, Vector] = {}
    for instance, body in replicas.items():
        for name, vector in parse_scrape(body, at=at).samples.items():
            for labels, value in vector.items():
                samples.setdefault(name, {})[labels | {("instance", instance)}] = value
    return Scrape(at=at, samples=samples)


def _query(name: str, replicas: dict[str, str]) -> Vector:
    """Instant queries: evaluated at the second of two scrapes, 15s apart, of the same state."""
    result = evaluate(GROUP_CIRCUIT_QUERIES[name], _fleet(replicas, at=0.0), _fleet(replicas, at=15.0))
    assert isinstance(result, dict), result
    return result


BY_GROUP: Labels = frozenset({("group", GROUP)})


def test_the_documented_queries_find_the_replica_whose_circuit_is_open(runs):
    """One replica tripped, one recovered: the divergence #1357 was filed for."""
    scrapes = runs["single"]["scrapes"]
    fleet = {"replica-a": scrapes["tripped"], "replica-b": scrapes["recovered"]}

    assert _query("replicas_disagree", fleet) == {BY_GROUP: 1.0}
    assert _query("open_anywhere", fleet) == {BY_GROUP: 1.0}
    assert _query("open_on", fleet) == {BY_GROUP | {("instance", "replica-a")}: 1.0}


def test_replicas_that_agree_the_circuit_is_open_do_not_disagree(runs):
    tripped = runs["single"]["scrapes"]["tripped"]
    fleet = {"replica-a": tripped, "replica-b": tripped}

    assert _query("replicas_disagree", fleet) == {}
    assert _query("open_anywhere", fleet) == {BY_GROUP: 1.0}


@pytest.mark.parametrize("name", sorted(GROUP_CIRCUIT_QUERIES))
def test_replicas_that_agree_it_is_closed_answer_nothing(runs, name):
    recovered = runs["single"]["scrapes"]["recovered"]

    assert _query(name, {"replica-a": recovered, "replica-b": recovered}) == {}
