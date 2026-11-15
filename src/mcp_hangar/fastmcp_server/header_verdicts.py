"""What a tool's ``x-mcp-header`` annotations say about serving it (#1056, #1057, #2253).

One verdict for both ways a tool is called: the flat projection withholds the
tool from the listing, and ``hangar_call`` refuses the call. They judge the same
live schema from the projection registry, so a tool the flat surface withholds
is one ``hangar_call`` will not invoke.

A leaf module: it imports neither surface. The flat projection is imported back
by ``server.bootstrap`` (#894), so the verdicts cannot live beside the batch
gates, and the gates cannot import the projection. The groups that own a server
are passed in by the caller, which reads them from ``owners_by_member``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from mcp.shared.inbound import find_invalid_x_mcp_header

from mcp_hangar.domain.policies.header_exposure import HeaderExposurePolicy, get_header_exposure_policy

from .. import metrics as prometheus_metrics

logger = logging.getLogger(__name__)

#: The bounded reasons a tool is withheld for: the reason labels the flat path
#: counts in ``PROJECTION_WITHDRAWALS_TOTAL``, and the ``hangar.gate.reason`` a
#: refused ``hangar_call`` carries.
INVALID_X_MCP_HEADER = "invalid_x_mcp_header"
HEADER_EXPOSURE_REFUSE_BOOT = "header_exposure_refuse_boot"


@dataclass(frozen=True)
class HeaderVerdict:
    """Why a tool is withheld: a bounded *code*, and the *reason* the warning line logged."""

    code: str
    reason: str


#: Per-(tool, schema version) verdict on the tool's own ``x-mcp-header``
#: annotations. The answer depends only on the schema, so it is settled once
#: per digest rather than on every listing -- a metric that fires per request
#: measures traffic, not the catalogue (#1049).
# ponytail: never evicted; bounded by catalogue size x schema drift. Upgrade:
# clear it where the registry invalidates.
_ANNOTATION_VERDICTS: dict[tuple[str, str, str], str | None] = {}

#: Per-(tool, schema version, policy) verdict on what the tool asks a client to
#: expose. Keyed like `_ANNOTATION_VERDICTS` and for the same reason: the answer
#: depends on the schema and the block, never on how often the tool is listed.
_EXPOSURE_VERDICTS: dict[tuple[str, str, str, Any], str | None] = {}


def invalid_header_annotation(proj: Any) -> str | None:
    """Why a conforming client must drop this tool, or ``None`` (#1056).

    SEP-2243 makes it a client-side **MUST**: a tool whose ``x-mcp-header``
    annotations fail ``find_invalid_x_mcp_header`` is dropped by the client
    that receives it. Projecting it anyway advertises a tool nobody can call
    and counts it as governance surface we delivered.

    The annotation is not stripped in place: the JCS digest is taken over
    ``{name, description, inputSchema, outputSchema}``, so editing the schema
    would move the digest and read as upstream drift. The tool goes away
    instead, with a reason.
    """
    key = (proj.mcp_server, proj.tool, proj.digest.sha256)
    if key in _ANNOTATION_VERDICTS:
        return _ANNOTATION_VERDICTS[key]

    reason = find_invalid_x_mcp_header(proj.schema.get("inputSchema"))
    _ANNOTATION_VERDICTS[key] = reason
    if reason is not None:
        logger.warning(
            "tool_withheld_invalid_x_mcp_header mcp_server=%s tool=%s reason=%s",
            proj.mcp_server,
            proj.tool,
            reason,
        )
        prometheus_metrics.PROJECTION_WITHDRAWALS_TOTAL.inc(reason=INVALID_X_MCP_HEADER)
    return reason


def exposure_policies(mcp_server: str, owning_groups: tuple[str, ...]) -> list[HeaderExposurePolicy]:
    """The `header_exposure` blocks that govern *mcp_server*.

    Its own block when it declares one. Otherwise a member inherits the block
    each group that owns it declared (#1038 scope shape), and a member of
    several groups is held to every one of them.
    """
    own = get_header_exposure_policy(mcp_server)
    if own:
        return [own]
    blocks = (get_header_exposure_policy(group) for group in owning_groups)
    return [block for block in blocks if block]


def can_withhold(mcp_server: str, owning_groups: tuple[str, ...]) -> bool:
    """Whether a block governing *mcp_server* withholds a tool it denies, rather than only warning."""
    return any(policy.on_violation != "warn" for policy in exposure_policies(mcp_server, owning_groups))


def denied_header_exposure(proj: Any, owning_groups: tuple[str, ...]) -> HeaderVerdict | None:
    """The `header_exposure` verdict for this tool, or ``None`` to keep it (#1057).

    SEP-2243's only defence against annotating a secret is a SHOULD NOT. An
    upstream that annotates `api_key` obliges every conforming client to put
    the key in an HTTP header, where every intermediary on the path can read
    it. This is the enforcement point that SHOULD is missing.

    Returns a verdict only when the tool must be withheld. ``warn`` -- the
    default, so adopting the block changes nobody's surface -- logs and counts
    but keeps the tool. ``refuse_boot`` is returned with its own code: the flat
    path raises on it, a call is refused.

    The schema is never edited; see `invalid_header_annotation`.
    """
    for policy in exposure_policies(proj.mcp_server, owning_groups):
        verdict = _exposure_verdict(proj, policy)
        if verdict is not None:
            return verdict
    return None


def _exposure_verdict(proj: Any, policy: HeaderExposurePolicy) -> HeaderVerdict | None:
    """What one `header_exposure` block says about this tool: see `denied_header_exposure`."""
    # Keyed on the policy VALUE, not its identity: a reload rebuilds the block,
    # and a recycled id() would answer for a policy that no longer exists.
    key = (proj.mcp_server, proj.tool, proj.digest.sha256, policy)
    if key not in _EXPOSURE_VERDICTS:
        _EXPOSURE_VERDICTS[key] = policy.violation(proj.schema.get("inputSchema"))
        if _EXPOSURE_VERDICTS[key] is not None:
            logger.warning(
                "tool_header_exposure_denied mcp_server=%s tool=%s action=%s reason=%s",
                proj.mcp_server,
                proj.tool,
                policy.on_violation,
                _EXPOSURE_VERDICTS[key],
            )
            prometheus_metrics.PROJECTION_WITHDRAWALS_TOTAL.inc(reason=f"header_exposure_{policy.on_violation}")

    reason = _EXPOSURE_VERDICTS[key]
    if reason is None or policy.on_violation == "warn":
        return None
    return HeaderVerdict(f"header_exposure_{policy.on_violation}", reason)


def withholding_verdict(proj: Any, owning_groups: tuple[str, ...]) -> HeaderVerdict | None:
    """Why this tool is not served, in the flat projection's order: its annotations, then its exposure."""
    invalid = invalid_header_annotation(proj)
    if invalid is not None:
        return HeaderVerdict(INVALID_X_MCP_HEADER, invalid)
    return denied_header_exposure(proj, owning_groups)
