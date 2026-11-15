"""``hangar_call`` refuses a tool the flat projection withholds for its header annotations (#2253).

`header_exposure` (#1057) and the invalid ``x-mcp-header`` check (#1056) used to
act only on the flat listing, so ``hangar_call`` still invoked a tool a
``withdraw`` block hid there. Both paths now ask `header_verdicts`, about the
same projection, and a refused call is an ADR-029 refusal: a bounded reason on
`batch_call_refused` and one `ToolCallRefused`.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from typing import Any
from unittest.mock import Mock, patch

import pytest
import structlog

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.events import ToolCallRefused
from mcp_hangar.domain.model.mcp_server_group import MemberSelection, RouteReason
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.policies.header_exposure import (
    HeaderExposurePolicy,
    clear_header_exposure_policies,
    set_header_exposure_policy,
)
from mcp_hangar.fastmcp_server import flat_tool_projection, header_verdicts
from mcp_hangar.server.tools.batch.tenant_admission import TenantLimits, configure_tenant_limits, reset_tenant_admission
from tests.unit import test_batch_gate_precedence as precedence
from tests.unit.test_batch_gate_precedence import _SERVER, _TENANT, _TOOL, _run

ctx = precedence.ctx
_reset_singletons = precedence._reset_singletons

_GROUP = "pool"

TOKEN_IN_HEADER = {
    "type": "object",
    "properties": {"api_token": {"type": "string", "x-mcp-header": "Api-Token"}},
}
REGION_IN_HEADER = {
    "type": "object",
    "properties": {"region": {"type": "string", "x-mcp-header": "Region"}},
}
#: An annotation a conforming client must drop the tool for: not on a property.
INVALID_ANNOTATION = {"type": "object", "x-mcp-header": "Whole-Body"}


@pytest.fixture(autouse=True)
def _clean_verdicts():
    header_verdicts._ANNOTATION_VERDICTS.clear()
    header_verdicts._EXPOSURE_VERDICTS.clear()
    configure_tenant_limits({_TENANT: TenantLimits(max_concurrency=5, rps=100, burst=100)})
    yield
    clear_header_exposure_policies()
    header_verdicts._ANNOTATION_VERDICTS.clear()
    header_verdicts._EXPOSURE_VERDICTS.clear()
    reset_tenant_admission()


def _serve(input_schema: dict[str, Any]) -> None:
    get_tool_projection_registry().build_from_tools(
        _SERVER, [ToolSchema(name=_TOOL, description="t", input_schema=input_schema)]
    )


def _refused(bus: Mock) -> list[ToolCallRefused]:
    return [c.args[0] for c in bus.publish.call_args_list if isinstance(c.args[0], ToolCallRefused)]


def _invoked(ctx: Any) -> bool:
    return any(type(c.args[0]).__name__ == "InvokeToolCommand" for c in ctx.command_bus.send.call_args_list)


def _run_logged() -> tuple[Any, list[dict[str, Any]]]:
    with structlog.testing.capture_logs() as captured:
        result = _run()
    return result, [line for line in captured if line["event"] == "batch_call_refused"]


def _assert_refused(ctx: Any, result: Any, lines: list[dict[str, Any]], gate: str, reason: str) -> None:
    assert result.success is False
    assert result.error_type == "ToolHeaderWithheldError"
    [line] = lines
    assert (line["gate"], line["reason"]) == (gate, reason)
    [event] = _refused(ctx.event_bus)
    assert (event.gate, event.gate_reason, event.tool_name) == (gate, reason, _TOOL)
    assert not _invoked(ctx), "a withheld tool never reaches its upstream"


class TestAWithdrawBlock:
    def test_on_the_server_refuses_the_call_with_the_reason_the_flat_path_logs(self, ctx, caplog) -> None:
        _serve(TOKEN_IN_HEADER)
        set_header_exposure_policy(_SERVER, HeaderExposurePolicy(("*token*",), "withdraw"))

        with caplog.at_level(logging.WARNING, logger=header_verdicts.__name__):
            result, lines = _run_logged()

        _assert_refused(ctx, result, lines, "header_exposure", "header_exposure_withdraw")
        projection = get_tool_projection_registry().resolve(_SERVER, _TOOL)
        flat_reason = flat_tool_projection._denied_header_exposure(projection)
        assert flat_reason is not None, "the flat projection withholds the same tool"
        [logged] = [r.getMessage() for r in caplog.records if "tool_header_exposure_denied" in r.getMessage()]
        assert logged.endswith(f"action=withdraw reason={flat_reason}")

    def test_on_a_group_refuses_a_call_naming_its_member(self, ctx) -> None:
        _serve(TOKEN_IN_HEADER)
        set_header_exposure_policy(_GROUP, HeaderExposurePolicy(("*token*",), "withdraw"))
        group = Mock(members=[Mock(id=_SERVER)])
        groups = {_GROUP: group}
        with ExitStack() as stack:
            stack.enter_context(patch("mcp_hangar.server.tools.batch.executor.GROUPS", groups))
            stack.enter_context(patch("mcp_hangar.server.tools.batch.validator.GROUPS", groups))
            result, lines = _run_logged()

        _assert_refused(ctx, result, lines, "header_exposure", "header_exposure_withdraw")

    def test_on_a_group_refuses_a_call_naming_the_group(self, ctx) -> None:
        _serve(TOKEN_IN_HEADER)
        set_header_exposure_policy(_GROUP, HeaderExposurePolicy(("*token*",), "withdraw"))
        member = ctx.get_mcp_server.return_value
        member.id.value = _SERVER
        group = Mock(members=[Mock(id=_SERVER)])
        group.select_member_with_reason.return_value = MemberSelection(member, RouteReason.LOAD_BALANCED)
        group.collect_events.return_value = []
        ctx.get_mcp_server.side_effect = lambda server_id: None if server_id == _GROUP else member
        groups = {_GROUP: group}
        with ExitStack() as stack:
            stack.enter_context(patch("mcp_hangar.server.tools.batch.executor.GROUPS", groups))
            stack.enter_context(patch("mcp_hangar.server.tools.batch.validator.GROUPS", groups))
            with structlog.testing.capture_logs() as captured:
                result = (
                    precedence.BatchExecutor()
                    .execute(
                        batch_id="b",
                        calls=[
                            precedence.CallSpec(index=0, call_id="c-1", mcp_server=_GROUP, tool=_TOOL, arguments={})
                        ],
                        max_concurrency=1,
                        global_timeout=30.0,
                        fail_fast=False,
                    )
                    .results[0]
                )

        lines = [line for line in captured if line["event"] == "batch_call_refused"]
        _assert_refused(ctx, result, lines, "header_exposure", "header_exposure_withdraw")

    def test_refuse_boot_refuses_the_call_rather_than_raising(self, ctx) -> None:
        _serve(TOKEN_IN_HEADER)
        set_header_exposure_policy(_SERVER, HeaderExposurePolicy(("*token*",), "refuse_boot"))

        result, lines = _run_logged()

        _assert_refused(ctx, result, lines, "header_exposure", "header_exposure_refuse_boot")


class TestAnInvalidAnnotation:
    def test_is_refused_with_no_block_at_all(self, ctx) -> None:
        _serve(INVALID_ANNOTATION)

        result, lines = _run_logged()

        _assert_refused(ctx, result, lines, "header_exposure", "invalid_x_mcp_header")
        projection = get_tool_projection_registry().resolve(_SERVER, _TOOL)
        assert flat_tool_projection._invalid_header_annotation(projection) is not None


class TestAToolThePolicyAllows:
    @pytest.mark.parametrize(
        ("schema", "policy"),
        [
            (REGION_IN_HEADER, HeaderExposurePolicy(("*token*",), "withdraw")),
            (TOKEN_IN_HEADER, HeaderExposurePolicy(("*token*",), "warn")),
            (TOKEN_IN_HEADER, None),
        ],
        ids=["not-denied", "warn-only", "no-block"],
    )
    def test_is_called_as_before(self, ctx, schema, policy) -> None:
        _serve(schema)
        if policy is not None:
            set_header_exposure_policy(_SERVER, policy)

        result, lines = _run_logged()

        assert result.success is True and lines == []
        assert _refused(ctx.event_bus) == []
        assert _invoked(ctx)


class TestAnUnknownSchema:
    def test_fails_closed_under_a_withdraw_block(self, ctx) -> None:
        """No catalogue even after the cold start: the verdict cannot be known."""
        ctx.get_mcp_server.return_value.state.value = "cold"
        set_header_exposure_policy(_SERVER, HeaderExposurePolicy(("*token*",), "withdraw"))

        result, lines = _run_logged()

        _assert_refused(ctx, result, lines, "deferred_header_exposure", "schema_unknown")

    def test_is_judged_once_the_cold_start_loaded_the_catalogue(self, ctx) -> None:
        ctx.get_mcp_server.return_value.state.value = "cold"
        set_header_exposure_policy(_SERVER, HeaderExposurePolicy(("*token*",), "withdraw"))
        ctx.command_bus.send.side_effect = lambda command: _serve(TOKEN_IN_HEADER) or {"ok": True}

        result, lines = _run_logged()

        _assert_refused(ctx, result, lines, "deferred_header_exposure", "header_exposure_withdraw")

    def test_goes_through_as_before_with_no_block_that_withholds(self, ctx) -> None:
        ctx.get_mcp_server.return_value.state.value = "cold"
        set_header_exposure_policy(_SERVER, HeaderExposurePolicy(("*token*",), "warn"))

        result, lines = _run_logged()

        assert result.success is True and lines == []
        assert _invoked(ctx)
