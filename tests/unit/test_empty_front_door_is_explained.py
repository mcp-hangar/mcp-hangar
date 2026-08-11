"""An empty front-door answer has to say which kind of empty it is (#862, #887).

Three very different situations produced the same 200, the same `{"tools": []}`
and nothing in the log:

* the caller carried no tenant, so the fail-closed branch denied everything;
* the replica has discovered nothing yet, so there is nothing to project;
* policy or withdrawal removed every tool -- the one case where `[]` is true.

That is what made #856 cost hours: every observable surface was healthy and the
only thing that disagreed produced no signal.
"""

from __future__ import annotations

import logging

import pytest

import mcp_hangar.server  # noqa: F401 -- import-order workaround, see #894
from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver
from mcp_hangar.fastmcp_server.flat_tool_projection import (
    EMPTY_FILTERED,
    EMPTY_NO_IDENTITY,
    EMPTY_NOTHING_DISCOVERED,
    _classify_empty_projection,
    _report_empty_projection,
)
from mcp_hangar.logging_config import reset_log_throttle, should_log_now


@pytest.fixture(autouse=True)
def _no_inherited_throttle():
    """The throttle is process-global; a test must never inherit a quiet period."""
    reset_log_throttle()
    yield
    reset_log_throttle()


@pytest.fixture
def _empty_registry():
    registry = get_tool_projection_registry()
    registry.invalidate()
    yield registry
    registry.invalidate()


class TestTheDenyAllBranchSpeaks:
    def test_it_names_the_missing_identity_branch(self, caplog: pytest.LogCaptureFixture) -> None:
        resolver = ToolAccessResolver()
        resolver.set_topology_mode("front_door")

        with caplog.at_level(logging.WARNING):
            allowed = resolver.is_tool_allowed(mcp_server_id="payments", tool_name="pay", member_id=None)

        assert allowed is False
        assert "front_door_denied_no_tenant" in caplog.text
        # The distinction the operator needs: not a policy decision.
        assert "NOT a policy decision" in caplog.text
        assert "payments" in caplog.text

    def test_a_tenant_that_is_merely_denied_by_policy_is_not_reported_as_missing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The point is to separate the two; a policy denial must stay quiet here."""
        resolver = ToolAccessResolver()
        resolver.set_topology_mode("front_door")

        with caplog.at_level(logging.WARNING):
            resolver.is_tool_allowed(mcp_server_id="payments", tool_name="pay", member_id="tenant:a")

        assert "front_door_denied_no_tenant" not in caplog.text

    def test_egress_mode_is_untouched(self, caplog: pytest.LogCaptureFixture) -> None:
        """In egress an identity-less caller is normal, not a symptom."""
        resolver = ToolAccessResolver()

        with caplog.at_level(logging.WARNING):
            resolver.is_tool_allowed(mcp_server_id="payments", tool_name="pay", member_id=None)

        assert "front_door_denied_no_tenant" not in caplog.text

    def test_it_does_not_flood(self, caplog: pytest.LogCaptureFixture) -> None:
        """The branch fires per request; a standing state must not bury the signal."""
        resolver = ToolAccessResolver()
        resolver.set_topology_mode("front_door")

        with caplog.at_level(logging.WARNING):
            for _ in range(50):
                resolver.is_tool_allowed(mcp_server_id="payments", tool_name="pay", member_id=None)

        assert caplog.text.count("front_door_denied_no_tenant") == 1


class TestTheEmptyProjectionIsClassified:
    def test_no_identity(self, _empty_registry) -> None:
        assert _classify_empty_projection(None) == EMPTY_NO_IDENTITY

    def test_nothing_discovered(self, _empty_registry) -> None:
        assert _classify_empty_projection("tenant:a") == EMPTY_NOTHING_DISCOVERED

    def test_filtered_is_the_correct_answer_case(self, _empty_registry) -> None:
        _empty_registry.build_from_tools("payments", [ToolSchema(name="pay", description="", input_schema={})])

        assert _classify_empty_projection("tenant:a") == EMPTY_FILTERED

    def test_a_cold_replica_says_so(self, _empty_registry, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            _report_empty_projection("tenant:a")

        assert "reason=nothing_discovered" in caplog.text
        assert "tenant:a" in caplog.text

    def test_a_correct_empty_answer_is_not_a_warning(self, _empty_registry, caplog: pytest.LogCaptureFixture) -> None:
        """Policy removing every tool is not a fault; warning on it trains people to ignore warnings."""
        _empty_registry.build_from_tools("payments", [ToolSchema(name="pay", description="", input_schema={})])

        with caplog.at_level(logging.DEBUG):
            _report_empty_projection("tenant:a")

        assert "reason=filtered" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_each_cause_is_counted_as_its_own_series(self, _empty_registry) -> None:
        """One lumped total would not answer the question the dashboard is asking."""

        def counted(reason: str) -> float:
            samples = prometheus_metrics.EMPTY_PROJECTION_TOTAL.collect()
            return next((s.value for s in samples if s.labels.get("reason") == reason), 0.0)

        before_no_identity = counted(EMPTY_NO_IDENTITY)
        before_cold = counted(EMPTY_NOTHING_DISCOVERED)

        _report_empty_projection(None)
        _report_empty_projection("tenant:a")
        _report_empty_projection("tenant:a")

        assert counted(EMPTY_NO_IDENTITY) == before_no_identity + 1
        assert counted(EMPTY_NOTHING_DISCOVERED) == before_cold + 2

    def test_the_counter_is_not_throttled_with_the_log(self, _empty_registry) -> None:
        """The log is throttled; the metric must not be, or the rate is a lie."""

        def cold_count() -> float:
            samples = prometheus_metrics.EMPTY_PROJECTION_TOTAL.collect()
            return next((s.value for s in samples if s.labels.get("reason") == EMPTY_NOTHING_DISCOVERED), 0.0)

        before = cold_count()
        for _ in range(10):
            _report_empty_projection("tenant:a")

        assert cold_count() == before + 10


class TestTheThrottle:
    def test_the_first_call_passes_and_the_second_does_not(self) -> None:
        assert should_log_now("k") is True
        assert should_log_now("k") is False

    def test_keys_are_independent(self) -> None:
        assert should_log_now("a") is True
        assert should_log_now("b") is True

    def test_a_zero_interval_never_throttles(self) -> None:
        assert should_log_now("k", interval_s=0) is True
        assert should_log_now("k", interval_s=0) is True
