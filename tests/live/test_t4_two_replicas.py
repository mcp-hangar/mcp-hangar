"""Tier 4 live verification: two gateways over one PostgreSQL (#1949, ADR-030 §7-§9).

The rows of ADR-030's acceptance table marked live, driven black-box: two real
`mcp-hangar serve --http` processes over one database, MCP servers over HTTPS
pinned by their keys, and calls made the way a client makes them. What a
replica holds is read over its API; what was recorded is read from the
database directly, so a replica cannot vouch for itself.

* T11: a key list or endpoint changed on A reaches B, whose copy is replaced
  without B publishing anything, and the removed key is refused on both.
* T11a: B's writes from a stale copy never bring a removed key back; a create
  on a replica that does not hold the server never overwrites it (#1954); a
  deleted key-pinned name comes back only with its `key_ref` and the
  permission.
* T23 and T24, the two-replica parts: the name record is seen by a second
  replica and a fresh one, and only one of two concurrent registrations of
  one `key_ref` succeeds.
* The forget racing a re-create, from the review of #1975.
* A mixed-version set: the published 2.31.0 next to this checkout.

Opt-in twice: `MCP_HANGAR_LIVE_VERIFY=1`, and `HANGAR_TEST_POSTGRES_DSN` naming
a server the test may create databases on. Locally,

    docker run -d --name ha-pg -e POSTGRES_PASSWORD=hangar -e POSTGRES_USER=hangar \\
        -e POSTGRES_DB=hangar -p 55432:5432 docker.io/library/postgres:16-alpine
    HANGAR_TEST_POSTGRES_DSN='host=127.0.0.1 port=55432 user=hangar password=hangar dbname=hangar' \\
        MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t4"
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from tests.live import _two_replicas as tr
from tests.live.conftest import running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t4]

pytest.importorskip("psycopg2", reason="the postgres extra is not installed")

DSN = os.environ.get(tr.DSN_ENV, "")
if not DSN:
    pytest.skip(f"{tr.DSN_ENV} is not set", allow_module_level=True)

OP, DEV = "operator", "developer"
_ROLES = {OP: "kubernetes-operator", DEV: "developer"}

# The release before the peer identity (ADR-030), for the mixed-version run.
_PUBLISHED = "2.31.0"
_PUBLISHED_REQUIREMENT = f"mcp-hangar[postgres]=={_PUBLISHED}"

_POLICY = {"tools": {"allow": ["echo"]}, "defaultAction": "Deny", "mode": "Enforce"}

_REFUSED = "UpstreamPeerKeyMismatchError"


@dataclass
class Fleet:
    """Two replicas over one database, two upstreams with a key each, and room for more replicas."""

    database: tr.Database
    workdir: Path
    config: str
    keys: dict[str, str]
    served: tr.TlsKey
    other: tr.TlsKey
    endpoints: dict[str, str]
    stack: ExitStack
    replicas: dict[str, tr.Replica] = field(default_factory=dict)

    def start(self, label: str, binary: str | None = None) -> tr.Replica:
        workdir = self.workdir / label
        workdir.mkdir()
        hangar = self.stack.enter_context(running_hangar(workdir, self.config, binary=binary))
        replica = tr.Replica(label=label, base_url=hangar.base_url, keys=self.keys)
        self.replicas[label] = replica
        return replica

    @property
    def a(self) -> tr.Replica:
        return self.replicas["a"]

    @property
    def b(self) -> tr.Replica:
        return self.replicas["b"]


@pytest.fixture(scope="module")
def fleet(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Fleet]:
    address = tr.host_address()
    if address is None:
        pytest.skip("no non-loopback address to serve the upstreams on; the API refuses loopback endpoints")
    workdir = tmp_path_factory.mktemp("two_replicas")
    with ExitStack() as stack:
        database = stack.enter_context(tr.fresh_database(DSN))
        keys = tr.seed_keys(database, list(_ROLES))
        served, other = tr.make_key(workdir, "served", address), tr.make_key(workdir, "other", address)
        endpoints = {
            "served": stack.enter_context(tr.upstream(served, address, workdir)),
            "other": stack.enter_context(tr.upstream(other, address, workdir)),
        }
        fleet = Fleet(
            database=database,
            workdir=workdir,
            config=tr.replica_config(database, address, _ROLES),
            keys=keys,
            served=served,
            other=other,
            endpoints=endpoints,
            stack=stack,
        )
        fleet.start("a")
        fleet.start("b")
        yield fleet


def _held_by(replica: tr.Replica, server: str, state: str | None = None) -> Callable[[], bool | None]:
    """A probe: ``replica`` holds ``server`` (in ``state``, when given)."""

    def probe() -> bool | None:
        held = replica.state(OP, server)
        return True if held is not None and (state is None or held == state) else None

    return probe


def _gone_from(replica: tr.Replica, server: str) -> Callable[[], bool | None]:
    return lambda: True if replica.state(OP, server) is None else None


def _assert_served(result: dict[str, Any], where: str) -> None:
    assert result.get("success") is True, f"{where} did not serve the call: {result}"


def _assert_refused(result: dict[str, Any], where: str) -> None:
    assert result.get("success") is False and result.get("error_type") == _REFUSED, (
        f"{where} did not refuse the key: {result}"
    )


def _pinned(fleet: Fleet, prefix: str, pins: list[str]) -> tuple[str, str]:
    """A key-pinned server at the `served` upstream, created on A and held by B; its name and key_ref."""
    server, ref = tr.name(prefix), tr.name("ref")
    created = fleet.a.create(OP, tr.peer_key_server(server, fleet.endpoints["served"], ref, pins))
    assert created.status_code == 201, created.text
    tr.eventually(_held_by(fleet.b, server), f"B holds {server}")
    return server, ref


# --- T11 -------------------------------------------------------------------------


def test_t11_a_key_removed_on_one_replica_is_refused_on_both(fleet: Fleet) -> None:
    """T11: A removes a key; B replaces its copy without publishing, and both refuse the removed key."""
    a, b, served, other = fleet.a, fleet.b, fleet.served, fleet.other
    server, ref = _pinned(fleet, "t11-keys", [served.pin])
    _assert_served(a.call(DEV, server), "A")
    _assert_served(b.call(DEV, server), "B")

    # A rotation: add the next key, then remove the current one.
    assert a.update(OP, server, tr.key_list(ref, [served.pin, other.pin])).status_code == 200
    tr.eventually(_held_by(b, server, "cold"), "B replaces its copy after the first edit")
    # Both hold a verified connection on the key about to be removed.
    _assert_served(a.call(DEV, server), "A, on the widened list")
    _assert_served(b.call(DEV, server), "B, on the widened list")

    before = fleet.database.position()
    assert a.update(OP, server, tr.key_list(ref, [other.pin])).status_code == 200
    assert tr.recorded_pins(fleet.database.row(server)) == [other.pin]
    tr.eventually(_held_by(b, server, "cold"), "B replaces its copy with a cold one built from the row")
    assert fleet.database.events_produced_by(b.instance_id(OP), server, before) == [], (
        "B published while replacing its copy"
    )

    _assert_refused(b.call(DEV, server), "B")
    _assert_refused(a.call(DEV, server), "A, the replica that handled the update, with a pooled connection")


def test_t11_an_endpoint_moved_on_one_replica_is_refused_on_both(fleet: Fleet) -> None:
    """T11: A points the server at a peer with an unlisted key; both refuse it."""
    a, b = fleet.a, fleet.b
    server, _ref = _pinned(fleet, "t11-endpoint", [fleet.served.pin])
    _assert_served(b.call(DEV, server), "B")

    assert a.update(OP, server, {"endpoint": fleet.endpoints["other"]}).status_code == 200
    tr.eventually(_held_by(b, server, "cold"), "B replaces its copy")

    _assert_refused(b.call(DEV, server), "B")
    _assert_refused(a.call(DEV, server), "A")


# --- T11a ------------------------------------------------------------------------


def _stale_attempts(attempt: Callable[[], bool], what: str, tries: int = 4) -> None:
    """Run ``attempt`` until one ran against a stale replica.

    The tail delivers a peer's change within its 2 s interval, so a write sent
    right after the change nearly always meets the old copy. ``attempt``
    returns whether it did; its own assertions hold either way.
    """
    if not any(attempt() for _ in range(tries)):
        pytest.fail(f"B was never stale in {tries} tries: {what}")


def test_t11a_stale_writes_never_bring_a_removed_key_back(fleet: Fleet) -> None:
    """T11a: B's description, L7 and endpoint writes from a stale copy leave the removed key removed."""
    a, b, served, other = fleet.a, fleet.b, fleet.served, fleet.other

    def attempt() -> bool:
        server, ref = _pinned(fleet, "t11a-stale", [served.pin, other.pin])
        _assert_served(b.call(DEV, server), "B")

        assert a.update(OP, server, tr.key_list(ref, [other.pin])).status_code == 200
        stale = b.state(OP, server) == "ready"
        writes = [
            b.update(OP, server, {"description": "from a stale copy"}),
            b.request("PUT", f"/{server}/l7_policy", OP, _POLICY),
            b.update(OP, server, {"endpoint": fleet.endpoints["served"]}),
        ]
        for write in writes:
            assert write.status_code in (200, 409), f"{write.request.method} {write.request.url}: {write.text}"
            assert tr.recorded_pins(fleet.database.row(server)) == [other.pin], "a stale write restored the key"

        tr.eventually(_held_by(b, server, "cold"), "B replaces its copy")
        _assert_refused(b.call(DEV, server), "B")
        _assert_refused(a.call(DEV, server), "A")
        return stale

    _stale_attempts(attempt, "a key removal followed by B's writes")


def test_t11a_a_create_on_a_replica_that_does_not_hold_the_server_never_overwrites_it(fleet: Fleet) -> None:
    """#1954: B, not yet holding a plain server A created, cannot create it again over A's row."""
    a, b = fleet.a, fleet.b

    def attempt() -> bool:
        server = tr.name("t11a-create")
        body = {"mcp_server_id": server, "mode": "remote", "endpoint": fleet.endpoints["other"]}
        assert a.create(OP, {**body, "description": "from a"}).status_code == 201
        stale = b.state(OP, server) is None
        again = b.create(OP, {**body, "description": "from b"})
        assert again.status_code != 201, f"B overwrote the row: {again.text}"
        assert (fleet.database.row(server) or {}).get("description") == "from a"
        return stale

    _stale_attempts(attempt, "a create on A followed by one on B")


def test_t11a_t23_a_deleted_name_comes_back_only_with_its_key_ref_and_the_permission(fleet: Fleet) -> None:
    """T11a, T23: after a delete on A, B and a freshly started replica refuse every create but the right one."""
    a, b, served = fleet.a, fleet.b, fleet.served
    server, ref = _pinned(fleet, "t11a-delete", [served.pin])
    assert a.delete(OP, server).status_code == 200
    tr.eventually(_gone_from(b, server), "B drops the deleted server")
    assert fleet.database.recorded_key_ref(server) == ref, "the name lost its key after the delete"

    plain = {"mcp_server_id": server, "mode": "remote", "endpoint": fleet.endpoints["served"]}
    fresh = fleet.start("t23-fresh")
    for replica in (b, fresh):
        for principal, body in (
            (DEV, plain),
            (OP, plain),
            (OP, tr.peer_key_server(server, fleet.endpoints["served"], tr.name("ref"), [served.pin])),
            (DEV, tr.peer_key_server(server, fleet.endpoints["served"], ref, [served.pin])),
        ):
            refused = replica.create(principal, body)
            assert refused.status_code in (403, 409, 422), f"{replica.label}/{principal}: {refused.text}"
            assert fleet.database.row(server) is None, f"{replica.label}/{principal} recorded a row"

    created = b.create(OP, tr.peer_key_server(server, fleet.endpoints["served"], ref, [served.pin]))
    assert created.status_code == 201, created.text
    _assert_served(b.call(DEV, server), "B")


# --- T23 / T24 -------------------------------------------------------------------


def test_t23_a_developer_cannot_change_a_key_pinned_server_another_replica_created(fleet: Fleet) -> None:
    """T23: on B, a `developer` key cannot change the key list or endpoint of A's server, or delete it."""
    b = fleet.b
    server, ref = _pinned(fleet, "t23-developer", [fleet.served.pin])
    for response in (
        b.update(DEV, server, tr.key_list(ref, [fleet.served.pin, fleet.other.pin])),
        b.update(DEV, server, {"endpoint": fleet.endpoints["other"]}),
        b.delete(DEV, server),
    ):
        assert response.status_code == 403, f"{response.request.method} {response.request.url}: {response.text}"
    assert tr.recorded_pins(fleet.database.row(server)) == [fleet.served.pin]
    _assert_served(b.call(DEV, server), "B")


def test_t24_one_key_ref_registered_on_both_replicas_at_once_goes_to_one_server(fleet: Fleet) -> None:
    """T24: two replicas registering one `key_ref` for two names concurrently; exactly one succeeds."""
    for _ in range(10):
        ref = tr.name("ref")
        first, second = (
            tr.peer_key_server(tr.name("t24-race"), fleet.endpoints["served"], ref, [fleet.served.pin]) for _ in "ab"
        )
        statuses = [
            r.status_code for r in tr.at_once(partial(fleet.a.create, OP, first), partial(fleet.b.create, OP, second))
        ]
        assert sorted(statuses)[0] == 201 and sorted(statuses)[1] != 201, statuses
        holders = fleet.database.query("SELECT name FROM peer_key_names WHERE key_ref = %s", (ref,))
        assert len(holders) == 1, f"{ref} names {holders}"


def test_t24_a_list_sharing_no_key_is_refused_on_another_replica_unless_the_key_ref_changes(fleet: Fleet) -> None:
    """T24: on B, a list disjoint from the stored one is refused under the same `key_ref`, accepted under a new one."""
    b, served, other = fleet.b, fleet.served, fleet.other
    server, ref = _pinned(fleet, "t24-disjoint", [served.pin])

    refused = b.update(OP, server, tr.key_list(ref, [other.pin]))
    assert refused.status_code in (409, 422), refused.text
    assert tr.recorded_pins(fleet.database.row(server)) == [served.pin]

    lineage = tr.name("ref")
    assert b.update(OP, server, tr.key_list(lineage, [other.pin])).status_code == 200
    assert tr.recorded_pins(fleet.database.row(server)) == [other.pin]
    assert fleet.database.recorded_key_ref(server) == lineage


# --- forget racing a re-create (#1975 review) ------------------------------------


def test_a_forget_racing_a_re_create_never_leaves_a_live_key_pinned_row_unrecorded(fleet: Fleet) -> None:
    """A forget on A and a key-pinned re-create on B, at once: a recorded key-pinned row keeps its entry."""
    a, b, served = fleet.a, fleet.b, fleet.served
    for _ in range(15):
        server, ref = _pinned(fleet, "forget-race", [served.pin])
        assert a.delete(OP, server).status_code == 200
        tr.eventually(_gone_from(b, server), "B drops the deleted server")

        body = tr.peer_key_server(server, fleet.endpoints["served"], ref, [served.pin])
        forgot, created = tr.at_once(partial(a.forget, OP, server), partial(b.create, OP, body))
        statuses = {"forget": forgot.status_code, "create": created.status_code}

        if tr.recorded_pins(fleet.database.row(server)) is not None:
            assert fleet.database.recorded_key_ref(server) == ref, f"a live key-pinned row lost its entry: {statuses}"


# --- mixed versions ------------------------------------------------------------------


@dataclass
class Published:
    """A replica running the published release, and the key-pinned servers registered before it started."""

    replica: tr.Replica
    servers: dict[str, str]


@pytest.fixture(scope="module")
def published(fleet: Fleet, tmp_path_factory: pytest.TempPathFactory) -> Published:
    """2.31.0 over the same database, started after this checkout's replicas.

    The servers are registered first, so they exist before the published
    replica rewrites the stored built-in roles at its start (#1988).
    """
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH to install the published release")
    venv = tmp_path_factory.mktemp("published") / "venv"
    try:
        subprocess.run([uv, "venv", "-q", "-p", "3.11", str(venv)], check=True, capture_output=True, timeout=120)
        subprocess.run(
            [uv, "pip", "install", "-q", "--python", str(venv / "bin" / "python"), _PUBLISHED_REQUIREMENT],
            check=True,
            capture_output=True,
            timeout=600,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not install {_PUBLISHED_REQUIREMENT}: {exc}")
    servers = {use: _pinned(fleet, f"mixed-{use}", [fleet.served.pin])[0] for use in ("serve", "create")}
    return Published(replica=fleet.start("published", binary=str(venv / "bin" / "mcp-hangar")), servers=servers)


def test_mixed_version_the_published_replica_never_serves_a_key_pinned_server(
    fleet: Fleet, published: Published
) -> None:
    """2.31.0 next to this checkout: it holds no copy, so it neither serves nor changes the server."""
    old, server = published.replica, published.servers["serve"]
    _assert_served(fleet.b.call(DEV, server), "B")

    assert old.state(OP, server) is None, "the published replica holds a copy of a key-pinned server"
    assert old.call(DEV, server).get("success") is not True
    for response in (
        old.update(OP, server, {"description": "from the published replica"}),
        old.request("PUT", f"/{server}/l7_policy", OP, _POLICY),
        old.delete(OP, server),
    ):
        assert response.status_code == 404, f"{response.request.method} {response.request.url}: {response.text}"
    assert tr.recorded_pins(fleet.database.row(server)) == [fleet.served.pin]


@pytest.mark.xfail(
    reason="#1987: 2.31.0 runs no name record check, and nothing in the database refuses it", strict=True
)
def test_mixed_version_a_create_on_the_published_replica_cannot_downgrade_the_row(
    fleet: Fleet, published: Published
) -> None:
    """2.31.0 knows no peer-key name record: its create under a key-pinned name must not record a plain row."""
    old, server = published.replica, published.servers["create"]

    created = old.create(DEV, {"mcp_server_id": server, "mode": "remote", "endpoint": fleet.endpoints["served"]})
    assert tr.recorded_pins(fleet.database.row(server)) == [fleet.served.pin], (
        f"the published replica recorded a plain row ({created.status_code})"
    )
    assert old.state(OP, server) is None, "the published replica serves the name without its key"


def test_mixed_version_the_published_replica_leaves_the_built_in_roles_alone(
    fleet: Fleet, published: Published
) -> None:
    """After 2.31.0 starts, this checkout's replicas still let the operator's key declare a peer identity."""
    assert published.replica.state(OP, published.servers["serve"]) is None
    server = tr.name("mixed-roles")
    created = fleet.a.create(
        OP, tr.peer_key_server(server, fleet.endpoints["served"], tr.name("ref"), [fleet.served.pin])
    )
    assert created.status_code == 201, created.text
