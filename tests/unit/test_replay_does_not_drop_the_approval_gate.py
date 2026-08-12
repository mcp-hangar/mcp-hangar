"""A restart must not remove a consent gate the configuration still declares (#915).

The tool-access-policy store held two lists, `allow_list` and `deny_list`. The
startup replay rebuilt a policy from exactly those two and called
`set_mcp_server_policy`, which assigns rather than merges. So the sequence was:

    bootstrap()  line 393   load_config()        -- YAML registers the gated policy
    bootstrap()  line 467   load_components()    -- replay overwrites it, ungated
    bootstrap()  line 605   reachability check   -- sees no approval_list, boots green

A target with `tools.approval_list` in YAML and any prior REST policy update came
back ungated after a restart, and the guard built for exactly this class of
failure had nothing left to demand the gate. Fail-open, and silent.

The tests are written against the two seams that produced it -- the store's
round trip and `_replay_tap_policies` against a live resolver -- rather than
against a mock of either, because the defect was that the two disagreed about
what a policy is.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mcp_hangar.auth.bootstrap import _replay_tap_policies
from mcp_hangar.auth.commands.commands import SetToolAccessPolicyCommand
from mcp_hangar.auth.commands.handlers import SetToolAccessPolicyHandler
from mcp_hangar.auth.infrastructure.sqlite_tap_store import SQLiteToolAccessPolicyStore
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

GATED = ToolAccessPolicy(
    deny_list=("internal_*",),
    approval_list=("refund_*",),
    approval_timeout_seconds=600,
    approval_channel="pigeon-post",
)


@pytest.fixture(autouse=True)
def _clean_resolver():
    """The resolver is a process-global. Put it back."""
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


@pytest.fixture
def store(tmp_path):
    tap = SQLiteToolAccessPolicyStore(tmp_path / "tap.db")
    yield tap
    tap.close()


class TestTheStoreHoldsAWholePolicy:
    def test_the_approval_gate_round_trips(self, store):
        store.set_policy("provider", "payments", GATED)

        assert store.get_policy("provider", "payments") == GATED

    def test_the_gate_survives_a_listing_for_replay(self, store):
        store.set_policy("provider", "payments", GATED)

        assert store.list_all_policies() == [("provider", "payments", GATED)]


class TestReplayDoesNotEraseWhatYamlDeclared:
    def test_a_stored_row_does_not_ungate_a_yaml_gated_target(self, store):
        """The reported bug, end to end on the two real objects."""
        resolver = get_tool_access_resolver()
        # 1. YAML load registers the gated policy (server/config.py:414).
        resolver.set_mcp_server_policy("payments", GATED)
        # 2. An earlier REST update wrote a row for the same target, from a
        #    build whose store had no approval columns at all.
        store.set_policy("provider", "payments", ToolAccessPolicy(deny_list=("internal_*",)))

        _replay_tap_policies(store)

        after = resolver.get_configured_policy("provider", "payments")
        assert after is not None
        assert after.requires_approval("refund_payment"), "the consent gate was replayed away"
        assert after.approval_list == GATED.approval_list
        assert after.approval_timeout_seconds == GATED.approval_timeout_seconds
        assert after.approval_channel == GATED.approval_channel

    def test_the_startup_guard_still_sees_something_to_demand_the_gate(self, store):
        """The check reads the resolver, so an erased gate reads as 'not configured'."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy("payments", GATED)
        store.set_policy("provider", "payments", ToolAccessPolicy(deny_list=("internal_*",)))

        _replay_tap_policies(store)

        gated = [scope for scope, policy in resolver.iter_registered_policies() if policy.approval_list]
        assert gated == ["mcp_server:payments"]

    def test_the_stored_access_lists_still_win(self, store):
        """Carrying the gate forward must not also freeze allow/deny."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy("payments", GATED)
        store.set_policy("provider", "payments", ToolAccessPolicy(deny_list=("internal_*", "wire_*")))

        _replay_tap_policies(store)

        after = resolver.get_configured_policy("provider", "payments")
        assert set(after.deny_list) == {"internal_*", "wire_*"}

    def test_a_target_with_no_gate_anywhere_gains_none(self, store):
        resolver = get_tool_access_resolver()
        store.set_policy("provider", "billing", ToolAccessPolicy(allow_list=("read_*",)))

        _replay_tap_policies(store)

        after = resolver.get_configured_policy("provider", "billing")
        assert after.approval_list == ()

    def test_a_stored_gate_is_replayed_on_a_cold_resolver(self, store):
        """Nothing in memory to carry from: the row itself has to carry it."""
        store.set_policy("provider", "payments", GATED)

        _replay_tap_policies(store)

        after = get_tool_access_resolver().get_configured_policy("provider", "payments")
        assert after.requires_approval("refund_payment")


class TestTheCommandPathWritesWhatItEnforces:
    def test_a_deny_list_update_persists_the_gate_it_preserved(self, store):
        """The resolver kept the gate; the store used to be handed less (#656 half-fixed this)."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy("payments", GATED)

        SetToolAccessPolicyHandler(tap_store=store, event_bus=MagicMock()).handle(
            SetToolAccessPolicyCommand(
                scope="provider",
                target_id="payments",
                allow_list=[],
                deny_list=["internal_*", "wire_*"],
            )
        )

        persisted = store.get_policy("provider", "payments")
        assert persisted.approval_list == GATED.approval_list
        assert persisted.approval_timeout_seconds == GATED.approval_timeout_seconds
        assert persisted.approval_channel == GATED.approval_channel
        assert set(persisted.deny_list) == {"internal_*", "wire_*"}

    def test_and_the_next_restart_replays_it_intact(self, store):
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy("payments", GATED)
        SetToolAccessPolicyHandler(tap_store=store, event_bus=MagicMock()).handle(
            SetToolAccessPolicyCommand(
                scope="provider",
                target_id="payments",
                allow_list=[],
                deny_list=["internal_*"],
            )
        )

        # A fresh process: nothing in memory, only what the store kept.
        reset_tool_access_resolver()
        _replay_tap_policies(store)

        after = get_tool_access_resolver().get_configured_policy("provider", "payments")
        assert after.requires_approval("refund_payment")


class TestAnOlderDatabaseIsWidenedInPlace:
    def test_a_pre_approval_table_gains_the_columns(self, tmp_path):
        """CREATE TABLE IF NOT EXISTS does nothing to an existing table."""
        import sqlite3

        db_path = tmp_path / "old.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE tool_access_policies (
                    scope       TEXT NOT NULL,
                    target_id   TEXT NOT NULL,
                    allow_list  TEXT NOT NULL DEFAULT '[]',
                    deny_list   TEXT NOT NULL DEFAULT '[]',
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (scope, target_id)
                )
                """
            )
            conn.execute(
                "INSERT INTO tool_access_policies (scope, target_id, allow_list, deny_list) VALUES (?, ?, ?, ?)",
                ("provider", "payments", "[]", '["internal_*"]'),
            )

        store = SQLiteToolAccessPolicyStore(db_path)
        try:
            # The old row reads back rather than exploding on missing columns...
            existing = store.get_policy("provider", "payments")
            assert existing == ToolAccessPolicy(deny_list=("internal_*",))
            # ...and the widened table can now hold a gate.
            store.set_policy("provider", "payments", GATED)
            assert store.get_policy("provider", "payments") == GATED
        finally:
            store.close()
