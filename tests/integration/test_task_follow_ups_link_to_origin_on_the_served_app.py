"""A task's follow-ups link to the call that created it, over real streamable HTTP (#1281).

The unit tests call the handlers with a stand-in context. Here the served app
does it: ``_front_door_task_governance_harness.py ... follow_ups`` bootstraps
Hangar in a fresh interpreter, serves ``serve --http``'s app under starlette's
``TestClient`` with API-key auth, and exports every span to memory. The
handlers run on the session task the transport starts, which is exactly where
a mock context proves nothing.

On each topology one caller creates a task with ``job`` -- the front door's
flat ``tools/call``, egress's ``hangar_call`` -- then polls, updates and
cancels it, each in its own request. Another tenant polls it in between, and
the owner polls it again once the cancel retired it. A caller that did not
declare the tasks extension then calls ``job``, so the seam cancels a task
nobody is handed.

What must hold, per ADR-029 s2, s3, s5 and s8: each follow-up is one
``task_relay.<op>`` span, a child of its request's SDK SERVER span and in that
request's trace, not the creating one, with exactly one link to the creating
``batch.call.job``. A foreign or retired task gets ``not_found``, no link and
no status. The seam's cancel is a new root linked to the refused call. No
Hangar span carries the task id.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.otel_sdk

HARNESS = Path(__file__).with_name("_front_door_task_governance_harness.py")
TOPOLOGIES = ("front_door", "egress")


def _run(topology: str, tmp: Path) -> dict[str, Any]:
    out = tmp / topology / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), topology, str(out), "follow_ups"],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{topology}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("task-follow-ups")
    with ThreadPoolExecutor(max_workers=len(TOPOLOGIES)) as pool:
        pending = {topology: pool.submit(_run, topology, tmp) for topology in TOPOLOGIES}
        return {topology: future.result() for topology, future in pending.items()}


def _named(run: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [span for span in run["spans"] if span["name"] == name]


def _by_id(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {span["span_id"]: span for span in run["spans"]}


def _origins(run: dict[str, Any]) -> list[dict[str, Any]]:
    """The two ``batch.call.job`` spans, in call order: the task handed over, then the one refused."""
    return _named(run, "batch.call.job")


def _follow_up_spans(run: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return _named(run, name)


@pytest.mark.parametrize("topology", TOPOLOGIES)
class TestEachFollowUpIsOneLinkedSpanOfItsOwnRequest:
    def test_the_harness_ran_this_checkout_and_the_calls_were_answered(self, runs, topology: str) -> None:
        run = runs[topology]
        assert run["hangar"].startswith(str(Path(__file__).parents[2])), run["hangar"]
        assert run["answers"]["created"]["outcome"] == "task"
        for method in ("get", "update", "cancel"):
            assert "result" in run["answers"][method], (method, run["answers"][method])
        assert ["tasks/cancel", run["task_id"]] in run["upstream_follow_ups"]

    @pytest.mark.parametrize(
        ("name", "server", "outcome"),
        [
            ("task_relay.get", "tasks/get", "served"),
            ("task_relay.update", "tasks/update", "relayed"),
            ("task_relay.cancel", "tasks/cancel", "confirmed"),
        ],
    )
    def test_it_is_a_child_of_its_server_span_linked_to_the_creating_call(
        self, runs, topology: str, name: str, server: str, outcome: str
    ) -> None:
        run = runs[topology]
        origin = _origins(run)[0]
        [span] = [s for s in _follow_up_spans(run, name) if s["attributes"].get("hangar.task.outcome") == outcome]
        parent = _by_id(run)[span["parent"]]

        assert (parent["name"], parent["kind"]) == (server, "SERVER")
        assert parent["parent"] is None, "the request's entry span, as no caller traceparent was sent"
        assert span["trace_id"] == parent["trace_id"] != origin["trace_id"]
        assert span["links"] == [[origin["trace_id"], origin["span_id"]]]
        assert span["attributes"]["mcp.server.id"] == "job-pool"
        assert span["attributes"]["gen_ai.tool.name"] == "job"
        assert span["status"] == "UNSET"

    def test_no_follow_up_opens_a_second_root(self, runs, topology: str) -> None:
        """ADR-029 Alternative 2: in each follow-up's trace, the SDK SERVER span is the only root."""
        run = runs[topology]
        follow_ups = [
            s for s in run["spans"] if s["name"] in ("task_relay.get", "task_relay.update", "task_relay.cancel")
        ]

        assert len(follow_ups) == 5
        for follow_up in follow_ups:
            roots = [s for s in run["spans"] if s["trace_id"] == follow_up["trace_id"] and s["parent"] is None]
            assert [(s["kind"], s["name"]) for s in roots] == [
                ("SERVER", follow_up["name"].replace("task_relay.", "tasks/"))
            ]

    def test_a_foreign_or_retired_task_is_not_found_with_no_link(self, runs, topology: str) -> None:
        run = runs[topology]
        assert run["answers"]["foreign_get"]["error"]["code"] == -32602
        assert run["answers"]["get_after_cancel"]["error"]["code"] == -32602
        missing = [
            s for s in _follow_up_spans(run, "task_relay.get") if s["attributes"]["hangar.task.outcome"] == "not_found"
        ]

        assert len(missing) == 2
        for span in missing:
            assert span["links"] == []
            assert "mcp.server.id" not in span["attributes"] and "gen_ai.tool.name" not in span["attributes"]
            assert span["status"] == "UNSET"
            assert span["attributes"]["error.type"] == "-32602"
            assert _by_id(run)[span["parent"]]["kind"] == "SERVER"

    def test_the_seams_cancel_is_a_new_root_linked_to_the_refused_call(self, runs, topology: str) -> None:
        run = runs[topology]
        assert run["answers"]["unhanded"]["outcome"] == "refused"
        refused = _origins(run)[1]
        [span] = _named(run, "task_relay.cancel_unhanded")

        assert span["parent"] is None
        assert span["trace_id"] != refused["trace_id"]
        assert span["links"] == [[refused["trace_id"], refused["span_id"]]]
        assert span["attributes"]["hangar.task.outcome"] == "confirmed"
        [upstream] = [s for s in run["spans"] if s["parent"] == span["span_id"]]
        assert (upstream["name"], upstream["kind"]) == ("tasks/cancel", "CLIENT")

    def test_no_hangar_span_carries_a_task_id(self, runs, topology: str) -> None:
        """Only the SDK-owned SERVER span's status description names one (ADR-029, Neutral)."""
        run = runs[topology]
        task_ids = {run["task_id"], *(tid for _method, tid in run["upstream_follow_ups"])}
        for span in run["spans"]:
            exported = dict(span)
            if span["kind"] == "SERVER":
                exported.pop("status_description")
            text = json.dumps(exported)
            assert not [tid for tid in task_ids if tid in text], span["name"]
