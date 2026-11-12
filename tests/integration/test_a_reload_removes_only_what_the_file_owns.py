"""A reload removes only the servers the file owns, and leaves what a restart would (#2169).

The reload diff counted every server in the repository that the file did not
name as deleted from it: one created with ``POST /api/mcp_servers``, one
registered by a discovery source. Each was stopped and dropped from the fleet
with its policies, and its row stayed, so a restart brought it back. A rescan
did not, because the source still counted it as registered.

``_reload_ownership_harness.py`` runs each phase in a fresh interpreter on one
working directory: the real ``bootstrap()`` from a file, real stdio subprocess
servers, and REST calls only. A phase's process exiting is the restart.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(300)

HARNESS = Path(__file__).with_name("_reload_ownership_harness.py")
PHASES = ("first", "restart", "takeover", "after_takeover", "dropped", "after_dropped", "upstream_restart")
#: A file server pushed its policies at runtime, then dropped by a reload (#2171).
FILE_SERVER_PHASES = ("edit", "upstream_reload", "after_upstream_reload")
NON_FILE = ("crud-one", "found-one")
AFTER_FIRST_RELOAD = ["crud-one", "found-one", "member", "upstream"]
FROM_THE_FILE = "as the file says"


def _phases(workdir: Path, backend: str, phases: tuple[str, ...]) -> dict[str, Any]:
    outcomes: dict[str, Any] = {}
    for phase in phases:
        out = workdir / f"{phase}.json"
        result = subprocess.run(
            [sys.executable, str(HARNESS), phase, backend, str(workdir), str(out)],
            capture_output=True,
            text=True,
            timeout=50,
        )
        assert result.returncode == 0 and out.exists(), f"{phase} exited {result.returncode}:\n{result.stderr[-6000:]}"
        outcomes[phase] = json.loads(out.read_text())
    return outcomes


@pytest.fixture(scope="module")
def sqlite(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _phases(tmp_path_factory.mktemp("reload-ownership-sqlite"), "sqlite", PHASES)


@pytest.fixture(scope="module")
def file_server(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _phases(tmp_path_factory.mktemp("reload-ownership-file-server"), "sqlite", FILE_SERVER_PHASES)


@pytest.fixture(scope="module", name="memory")
def in_memory(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    # Two phases: the rest build on servers a restart of this backend does not keep.
    return _phases(tmp_path_factory.mktemp("reload-ownership-memory"), "memory", PHASES[:2])


@pytest.fixture(params=["sqlite", "memory"])
def run(request: pytest.FixtureRequest) -> dict[str, Any]:
    """The same reload on either backend: what a server owns is not read from storage."""
    return dict(request.getfixturevalue(request.param))


def test_the_scenario_had_every_server_running_with_its_policies(run: dict[str, Any]) -> None:
    before = run["first"]["before"]
    assert before["ids"] == ["crud-one", "found-one", "gone", "gone-member", "member", "upstream"]
    for sid in NON_FILE:
        assert before["policies"][sid] == {"deny_list": ["add"], "l7_action": "Allow"}


def test_a_server_from_the_api_or_discovery_stays_running_and_untouched(run: dict[str, Any]) -> None:
    first = run["first"]
    assert first["ids"] == AFTER_FIRST_RELOAD
    assert first["same_object"] == dict.fromkeys(NON_FILE, True)
    assert first["running"] == dict.fromkeys(NON_FILE, True)
    assert first["states"] == dict.fromkeys(NON_FILE, "ready")
    assert not set(NON_FILE) & set(first["result"]["mcp_servers_updated"])


def test_they_keep_their_policies(run: dict[str, Any]) -> None:
    assert run["first"]["policies"] == dict.fromkeys(NON_FILE, {"deny_list": ["add"], "l7_action": "Allow"})


def test_a_server_removed_from_the_file_is_still_stopped_and_removed(run: dict[str, Any]) -> None:
    assert run["first"]["result"]["mcp_servers_removed"] == ["gone", "gone-member"]
    assert run["first"]["gone_exited"] is True


def test_a_server_of_the_file_edited_at_runtime_stays_the_files(run: dict[str, Any]) -> None:
    # Kept as the file's: the reload applies the file over the edit, as a restart does.
    assert run["first"]["upstream_description"] == FROM_THE_FILE
    assert "upstream" in run["first"]["result"]["mcp_servers_updated"]


def test_a_rescan_after_the_reload_leaves_the_discovered_server_registered(run: dict[str, Any]) -> None:
    rescan = run["first"]["rescan"]
    assert (rescan["status"], rescan["found"]) == (200, 1)
    assert rescan["ids"] == AFTER_FIRST_RELOAD
    assert rescan["pending"] == []
    assert run["first"]["same_object_after_rescan"] == dict.fromkeys(NON_FILE, True)
    assert run["first"]["running_after_rescan"] == dict.fromkeys(NON_FILE, True)


def test_a_non_file_server_keeps_its_group_memberships_across_the_reload(run: dict[str, Any]) -> None:
    assert run["first"]["before"]["groups"] == {
        "pool": ["crud-one", "gone-member", "member"],
        "runtime-group": ["crud-one"],
    }
    assert run["first"]["groups"] == {"pool": ["crud-one", "member"], "runtime-group": ["crud-one"]}
    assert "crud-one" in run["first"]["ids"]


def test_a_restart_on_a_durable_backend_gives_what_the_reload_left(sqlite: dict[str, Any]) -> None:
    first, booted = sqlite["first"], sqlite["restart"]["booted"]
    assert booted["ids"] == first["ids"]
    # The groups too: those made over REST are stored, so a restart has them (#2172).
    assert booted["groups"] == first["groups"]
    assert booted["policies"] == first["policies"]
    assert booted["upstream_description"] == first["upstream_description"]


def test_a_reload_and_a_rescan_after_that_restart_change_nothing(sqlite: dict[str, Any]) -> None:
    restart = sqlite["restart"]
    assert restart["removed_by_a_reload"] == []
    assert restart["ids_after_reload"] == AFTER_FIRST_RELOAD
    assert restart["policies_after_reload"] == restart["booted"]["policies"]
    assert restart["rescan"]["ids"] == AFTER_FIRST_RELOAD
    assert restart["rescan"]["pending"] == []


def test_the_in_memory_backend_loses_them_at_a_restart_not_at_a_reload(memory: dict[str, Any]) -> None:
    # The stated difference: nothing is on record, so a restart has the file and
    # what a source reports. A reload treats both backends alike.
    assert memory["first"]["ids"] == AFTER_FIRST_RELOAD
    assert memory["restart"]["booted"]["ids"] == ["member", "upstream"]
    assert memory["restart"]["rescan"]["pending"] == ["found-one"]


def test_a_file_that_newly_declares_an_id_takes_it_over(sqlite: dict[str, Any]) -> None:
    takeover = sqlite["takeover"]
    assert takeover["updated"] == list(NON_FILE)
    assert takeover["removed"] == []
    assert takeover["crud_process_exited"] is True
    assert takeover["described"] == dict.fromkeys(NON_FILE, FROM_THE_FILE)
    assert takeover["provenance"] == dict.fromkeys(NON_FILE, "human")
    # The file declares no L7 policy, and a takeover must not lift the pushed one.
    assert takeover["policies"] == dict.fromkeys(NON_FILE, {"deny_list": ["add"], "l7_action": "Allow"})


def test_a_taken_over_server_is_the_files_for_discovery_and_the_api(sqlite: dict[str, Any]) -> None:
    takeover = sqlite["takeover"]
    assert takeover["rescan"]["ids"] == AFTER_FIRST_RELOAD
    assert takeover["changed_definition"]["described"] == dict.fromkeys(NON_FILE, FROM_THE_FILE)
    assert takeover["approve"]["approved"] is False, takeover["approve"]
    assert takeover["described_after_approve"] == FROM_THE_FILE
    assert takeover["delete_status"] == 409


def test_a_restart_gives_what_the_takeover_left(sqlite: dict[str, Any]) -> None:
    assert sqlite["after_takeover"]["ids"] == sqlite["takeover"]["rescan"]["ids"]
    assert sqlite["after_takeover"]["described"] == sqlite["takeover"]["described"]


def test_a_taken_over_server_the_file_drops_again_goes(sqlite: dict[str, Any]) -> None:
    assert sqlite["dropped"]["removed"] == ["crud-one"]
    assert "crud-one" not in sqlite["dropped"]["ids"]


def test_a_server_the_file_took_over_and_dropped_does_not_come_back_at_a_restart(sqlite: dict[str, Any]) -> None:
    """The row the API wrote stays, claimed by the file at the takeover reload (#2171).

    SQLite is standalone, so the replica is its own manager and claims at its
    reload. The row is kept, since a replica whose file still declares the
    server reads its stored L7 policy from it at its next restart, and a restart
    on a file that no longer declares it skips it.
    """
    assert "crud-one" not in sqlite["after_dropped"]["ids"]


def test_a_file_server_edited_at_runtime_and_dropped_at_a_restart_does_not_come_back(sqlite: dict[str, Any]) -> None:
    # `first` edited it over REST, which wrote a row: the file's, not a registration (#2171).
    restart = sqlite["upstream_restart"]
    assert "upstream" not in restart["ids"]
    # The id is free: a create replaces the row the file left.
    assert restart["create_status"] == 201


def test_a_file_server_dropped_by_a_reload_does_not_come_back_at_a_restart(file_server: dict[str, Any]) -> None:
    assert file_server["edit"]["policies"] == {"deny_list": ["add"], "l7_action": "Allow"}
    assert file_server["upstream_reload"]["removed"] == ["upstream"]
    assert "upstream" not in file_server["upstream_reload"]["ids"]
    assert "upstream" not in file_server["after_upstream_reload"]["ids"]


def test_a_server_created_over_the_row_the_file_left_keeps_its_l7_policy(file_server: dict[str, Any]) -> None:
    # An L7 policy only narrows what runs, so it is carried; a peer still on the
    # old file reads it from this row at its next start.
    created = file_server["after_upstream_reload"]
    assert created["create_status"] == 201
    assert created["policies"]["l7_action"] == "Allow"
    # Its deny list here is the policy store's replay at boot, keyed by id, and
    # not the row's: the row holds no tool access policy to carry.
