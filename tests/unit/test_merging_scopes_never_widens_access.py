"""Merging a narrower scope onto a broader one can only remove tools.

`merge`'s own docstring states the invariant:

    merged.filter_tools(tools) == narrower.filter_tools(broader.filter_tools(tools))
    for ALL possible tool lists

It did not hold. `merge` dispatched on which lists were populated and each
branch rebuilt a piece of the deny > approval > allow > default ladder from the
lists its condition named, dropping the rest. "Both sides have an allow_list"
consulted neither `deny_list`, so a tool denied at the server scope came back
allowed the moment a group scope repeated the allow list -- a policy bypass
reachable from an ordinary two-level configuration.

The ladder lives in `is_tool_allowed`, once. Merging now composes the two
policies' own answers, so there is no second copy of the precedence rules left
to disagree with it.
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

TOOLS = ["read_file", "write_file", "drop_db", "transfer_funds"]


def _shapes() -> list[tuple[str, ToolAccessPolicy, ToolAccessPolicy]]:
    """One entry per branch `merge` used to dispatch on."""
    return [
        (
            "both-allow-broader-denies",  # the reported bypass
            ToolAccessPolicy(allow_list=["*"], deny_list=["drop_*"]),
            ToolAccessPolicy(allow_list=["*"]),
        ),
        (
            "both-allow-narrower-denies",
            ToolAccessPolicy(allow_list=["*"]),
            ToolAccessPolicy(allow_list=["*"], deny_list=["drop_*"]),
        ),
        (
            "broader-allow-and-deny-narrower-deny",
            ToolAccessPolicy(allow_list=["*"], deny_list=["drop_*"]),
            ToolAccessPolicy(deny_list=["transfer_*"]),
        ),
        (
            "broader-deny-narrower-allow-and-deny",
            ToolAccessPolicy(deny_list=["drop_*"]),
            ToolAccessPolicy(allow_list=["*"], deny_list=["transfer_*"]),
        ),
        (
            "both-deny",
            ToolAccessPolicy(deny_list=["drop_*"]),
            ToolAccessPolicy(deny_list=["transfer_*"]),
        ),
        (
            "approval-survives",
            ToolAccessPolicy(allow_list=["*"], approval_list=["transfer_*"]),
            ToolAccessPolicy(allow_list=["read_*", "transfer_*"]),
        ),
    ]


@pytest.mark.parametrize(("label", "broader", "narrower"), _shapes(), ids=[s[0] for s in _shapes()])
def test_merging_matches_applying_each_scope_in_turn(
    label: str, broader: ToolAccessPolicy, narrower: ToolAccessPolicy
) -> None:
    """The invariant `merge` documents, over every shape it dispatches on."""
    merged = ToolAccessPolicy.merge(broader, narrower)

    assert merged.filter_tools(TOOLS) == narrower.filter_tools(broader.filter_tools(TOOLS))


@pytest.mark.parametrize(("label", "broader", "narrower"), _shapes(), ids=[s[0] for s in _shapes()])
def test_a_tool_denied_by_either_scope_stays_denied(
    label: str, broader: ToolAccessPolicy, narrower: ToolAccessPolicy
) -> None:
    """Deny wins, in either argument order -- merging is not a negotiation."""
    for tool in TOOLS:
        denied_somewhere = not broader.is_tool_allowed(tool) or not narrower.is_tool_allowed(tool)
        if not denied_somewhere:
            continue
        assert not ToolAccessPolicy.merge(broader, narrower).is_tool_allowed(tool)
        assert not ToolAccessPolicy.merge(narrower, broader).is_tool_allowed(tool)


def test_the_reported_bypass() -> None:
    """The exact reproduction from the issue."""
    broader = ToolAccessPolicy(allow_list=["*"], deny_list=["drop_*"])
    narrower = ToolAccessPolicy(allow_list=["*"])

    assert not broader.is_tool_allowed("drop_db")
    assert not ToolAccessPolicy.merge(broader, narrower).is_tool_allowed("drop_db")


def test_merging_still_narrows() -> None:
    """The fix must not make merging a no-op: intersection still intersects."""
    broader = ToolAccessPolicy(allow_list=["read_*", "write_*"])
    narrower = ToolAccessPolicy(allow_list=["read_*"])

    merged = ToolAccessPolicy.merge(broader, narrower)

    assert merged.is_tool_allowed("read_file")
    assert not merged.is_tool_allowed("write_file")
