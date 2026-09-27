"""A handshake-era call is served under ``headers.param_validation.required``, its headers ignored (#1605).

ADR-025 Decision 2 treats ``legacy_protocol`` as an era rather than a failure:
a client on a revision that predates mandatory ``Mcp-Param-*`` validation does
not validate headers, by design. #1601 refused such a call under ``required``
with every other unvalidated one. The refusal protected nothing, because the
headers of a legacy call never reach the selector anyway.

"Legacy" is decided once, by ``predates_param_validation``, and both the L7
evaluator and ``required`` read it, so the two cannot disagree. Every modern
unvalidated case keeps its refusal.

Naming: neutral placeholders only (read_item, tenant:a).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from mcp_hangar._sdk_compat import HANDSHAKE_PROTOCOL_VERSIONS
from mcp_hangar.context import (
    PARAM_VALIDATED_HEADERS_ATTR,
    PARAM_VALIDATION_KEY,
    PARAM_VALIDATION_SKIPPED,
    PARAM_VALIDATION_STATE_ATTR,
    param_headers_unchecked,
    predates_param_validation,
)
from mcp_hangar.domain.policies.egress_l7 import HeaderMatch, HeaderRules, evaluate_headers
from mcp_hangar.fastmcp_server import flat_tool_projection as ftp
from mcp_hangar.server.tools.batch import _refuse_if_param_headers_unchecked
from mcp_hangar.tasks_wire import HEADER_MISMATCH

MODERN = "2026-07-28"
LEGACY = "2025-06-18"
TOOL = "read_item"
EU = HeaderMatch(name="Mcp-Param-Region", values=("eu-*",))


def _ctx(version: str | None, headers: dict[str, str] | None = None, **state: Any) -> SimpleNamespace:
    carried = dict(headers if headers is not None else {"mcp-param-region": "eu-west-1"})
    if version is not None:
        carried["mcp-protocol-version"] = version
    body = json.dumps({"method": "tools/call", "params": {"name": TOOL, "arguments": {"region": "eu-west-1"}}})
    request = SimpleNamespace(headers=carried, state=SimpleNamespace(**state), _body=body.encode())
    return SimpleNamespace(request=request)


@pytest.fixture()
def required():
    before = ftp.param_validation_required()
    ftp.set_param_validation_required(True)
    yield
    ftp.set_param_validation_required(before)


class TestTheSharedReading:
    @pytest.mark.parametrize("version", HANDSHAKE_PROTOCOL_VERSIONS)
    def test_every_handshake_revision_predates_it(self, version: str) -> None:
        assert predates_param_validation({"mcp-protocol-version": version}) is True

    def test_an_absent_version_is_handshake_era(self) -> None:
        """The spec's default for a request without the header is a handshake-era revision."""
        assert predates_param_validation({"mcp-param-region": "eu-west-1"}) is True

    def test_a_modern_revision_does_not(self) -> None:
        assert predates_param_validation({"mcp-protocol-version": MODERN}) is False

    @pytest.mark.parametrize("version", [LEGACY, MODERN, None])
    def test_the_evaluator_and_required_cannot_disagree(self, version: str | None) -> None:
        """Where the evaluator would ignore the headers for their era, ``required`` does not refuse them."""
        ctx = _ctx(version)
        mapping = {**ctx.request.headers, PARAM_VALIDATION_KEY: "ran"}

        consulted = evaluate_headers(mapping, HeaderRules(deny=(EU,))) is not None

        assert consulted is (not predates_param_validation(mapping))
        assert param_headers_unchecked(ctx) is consulted


class TestALegacyRequestIsNotUnchecked:
    @pytest.mark.parametrize(
        "state",
        [
            {},
            {PARAM_VALIDATED_HEADERS_ATTR: frozenset({"mcp-param-region"}), PARAM_VALIDATION_STATE_ATTR: True},
        ],
        ids=["nothing-recorded", "skip-recorded"],
    )
    def test_its_dropped_headers_are_ignored(self, state: dict[str, Any]) -> None:
        assert param_headers_unchecked(_ctx(LEGACY, **state)) is False

    def test_a_malformed_sentinel_on_a_legacy_request_is_ignored_too(self) -> None:
        ctx = _ctx(LEGACY, {"mcp-param-region": "=?base64?not base64?="})

        assert param_headers_unchecked(ctx) is False

    def test_its_headers_still_decide_nothing(self) -> None:
        mapping = {"mcp-param-region": "eu-west-1", "mcp-protocol-version": LEGACY, PARAM_VALIDATION_KEY: "ran"}

        assert evaluate_headers(mapping, HeaderRules(deny=(EU,))) is None


class TestEveryModernCaseIsStillUnchecked:
    @pytest.mark.parametrize(
        ("headers", "state"),
        [
            ({"mcp-param-region": "eu-west-1"}, {}),
            (
                {"mcp-param-region": "eu-west-1", "mcp-param-tier": "gold"},
                {PARAM_VALIDATED_HEADERS_ATTR: {"mcp-param-region"}},
            ),
            ({"mcp-param-region": "eu-west-1"}, {PARAM_VALIDATION_STATE_ATTR: True}),
            ({"mcp-param-region": "=?base64?not base64?="}, {PARAM_VALIDATED_HEADERS_ATTR: {"mcp-param-region"}}),
        ],
        ids=["hangar-call", "undeclared-header", "listing-skip", "malformed-sentinel"],
    )
    def test_it_counts_as_unchecked(self, headers: dict[str, str], state: dict[str, Any]) -> None:
        assert param_headers_unchecked(_ctx(MODERN, headers, **state)) is True

    def test_an_unreadable_request_still_counts_as_unchecked(self) -> None:
        class _Exploding:
            @property
            def request(self) -> Any:
                raise RuntimeError("no request")

        assert param_headers_unchecked(_Exploding()) is True


class TestHangarCall:
    def test_a_legacy_hangar_call_with_a_param_header_is_served(self, required) -> None:
        _refuse_if_param_headers_unchecked(SimpleNamespace(request_context=_ctx(LEGACY)))  # type: ignore[arg-type]

    def test_a_modern_one_is_still_refused(self, required) -> None:
        with pytest.raises(Exception) as caught:
            _refuse_if_param_headers_unchecked(SimpleNamespace(request_context=_ctx(MODERN)))  # type: ignore[arg-type]

        assert getattr(caught.value, "code", None) == HEADER_MISMATCH


class TestTheFrontDoor:
    @staticmethod
    def _call(ctx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> Any:
        """Drive the front door's tools/call over an empty map: an unrefused call ends in -32601."""
        monkeypatch.setattr(ftp, "_build_flat_map", lambda _tenant: {})
        monkeypatch.setattr("mcp_hangar.server.tools.tool_permissions.management_tools_for", lambda _ctx: set())
        handlers: dict[str, Any] = {}

        class _Low:
            def add_request_handler(self, method, params_type, handler):
                handlers[method] = handler

        ftp.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
        captured: list[BaseException] = []

        async def _run() -> None:
            try:
                await handlers["tools/call"](ctx, SimpleNamespace(name=TOOL, arguments={"region": "eu-west-1"}))
            except BaseException as exc:  # noqa: BLE001 -- the verdict is the subject of the test
                captured.append(exc)

        anyio.run(_run)
        return getattr(captured[0], "code", None) if captured else None

    def test_a_legacy_call_with_a_param_header_is_not_refused(self, required, monkeypatch) -> None:
        assert self._call(_ctx(LEGACY), monkeypatch) != HEADER_MISMATCH

    def test_a_modern_one_is_still_refused(self, required, monkeypatch) -> None:
        assert self._call(_ctx(MODERN), monkeypatch) == HEADER_MISMATCH


def test_the_skipped_value_is_what_a_legacy_request_binds() -> None:
    """The legacy headers are dropped, not admitted: ignoring them is not trusting them."""
    from mcp_hangar.context import bind_routing_headers, release_routing_headers, routing_headers_var

    token = bind_routing_headers(_ctx(LEGACY))
    try:
        bound = dict(routing_headers_var.get() or {})
    finally:
        release_routing_headers(token)

    assert bound[PARAM_VALIDATION_KEY] == PARAM_VALIDATION_SKIPPED
    assert "mcp-param-region" not in bound
