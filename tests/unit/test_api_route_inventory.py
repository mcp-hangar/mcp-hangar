"""The published REST inventory must match the routes actually served.

`api-routes.json` is a cross-repo contract (ADR-011): consumers vendor it and
check the URLs they build against it. That is only worth anything if it cannot
quietly fall behind, which is what this test is for -- a route added, removed or
renamed either updates the file or fails the build.

## Why the file exists at all

The operator built URLs against `/api/v1/*` for months after core moved to
`/api/*`. Every remote `MCPServer` sat `Degraded` while working, and the
operator's tests stayed green the whole time: they assert against an `httptest`
mock, and a mock answers whatever it is asked (operator#91).

Catching that never needed a live server. It needed an authoritative list of
paths, which nobody published.
"""

from __future__ import annotations

import json

import pytest

from scripts.dump_api_routes import INVENTORY_PATH, collect_routes


@pytest.fixture(scope="module")
def published() -> dict:
    if not INVENTORY_PATH.exists():
        pytest.fail(f"{INVENTORY_PATH.name} is missing; run `python scripts/dump_api_routes.py --write`")
    return json.loads(INVENTORY_PATH.read_text())


class TestTheInventoryIsCurrent:
    def test_it_matches_the_live_routing_table(self, published: dict) -> None:
        live = collect_routes()

        assert published["routes"] == live["routes"], (
            "api-routes.json has drifted from the served routes. "
            "Run `python scripts/dump_api_routes.py --write` and commit the result. "
            "Consumers vendor this file, so a stale copy sends them at endpoints "
            "that do not exist -- which is the bug it was added to prevent."
        )

    def test_the_count_field_agrees_with_the_list(self, published: dict) -> None:
        """Cheap guard against a hand-edit that adds a route but not the count."""
        assert published["count"] == len(published["routes"])


class TestTheContractIsUsable:
    def test_paths_carry_no_framework_converters(self, published: dict) -> None:
        """`{id:str}` is Starlette's business, not a consumer's.

        A consumer matching a concrete URL against these templates should not
        have to know which web framework produced them.
        """
        offenders = [r["path"] for r in published["routes"] if ":" in r["path"]]

        assert offenders == [], f"converter suffixes leaked into the contract: {offenders}"

    def test_every_path_is_mounted_under_the_api_prefix(self, published: dict) -> None:
        """The `/api` mount is applied by the caller, not the router.

        If that ever changes, the inventory would silently publish unprefixed
        paths and every consumer would build the wrong URL.
        """
        wrong = [r["path"] for r in published["routes"] if not r["path"].startswith(published["mount"])]

        assert wrong == [], f"paths outside the documented mount: {wrong}"

    def test_it_records_the_route_the_operator_depends_on(self, published: dict) -> None:
        """A named example, so the contract has at least one asserted consumer.

        The operator's remote health probe uses this exact path. It is spelled
        out here so that removing or renaming it fails in core -- where the
        decision is made -- rather than silently in another repository.
        """
        paths = {r["path"] for r in published["routes"]}

        assert "/api/mcp_servers/{mcp_server_id}/health" in paths


class TestKnownRegression:
    def test_the_api_v1_prefix_is_not_served(self, published: dict) -> None:
        """The prefix the operator spent months calling.

        Kept as a test rather than a comment: if `/api/v1/*` is ever
        reintroduced, that is a decision worth making deliberately, not by
        accident, and it changes what every existing consumer should do.
        """
        v1 = [r["path"] for r in published["routes"] if r["path"].startswith("/api/v1/")]

        assert v1 == [], f"/api/v1 is back: {v1}"
