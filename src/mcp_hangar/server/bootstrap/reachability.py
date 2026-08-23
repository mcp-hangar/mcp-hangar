"""Startup check: is every subsystem the configuration asks for actually wired?

Six times now a subsystem has been built on one construction path while the
shipped process took another, and every time the symptom was silence: the
governed task relay wired only in ``MCPServerFactory`` (#592), the flat-tool
projection and the governance descriptors likewise (#595, #596), the operator
calling a REST prefix core had moved (operator#91), and the approval gate --
config key, service and REST wiring all absent while ``requires_approval()``
sat there looking enforced (#678). In each case the process started clean and
did less than its configuration said.

This module closes the class rather than the instance. It asks one question at
the end of :func:`mcp_hangar.server.bootstrap.bootstrap` -- the funnel every
entry point passes through (``serve``, ``serve --http``, the facade) -- for each
subsystem that has a configuration that can *demand* it:

    the config demands it   AND   the runtime object that serves it is absent

A demand met by absence is never silent again. Security subsystems (the
approval gate) refuse the boot: a config that says "hold this tool for a human"
and a gateway that cannot hold it is a gateway executing unapproved calls, and
starting anyway is failing open. Everything else logs at ERROR naming the
subsystem and what asked for it.

Set ``startup_checks: {enforce: false}`` to downgrade the refusals to error
logs. There is deliberately no switch that makes an unreachable subsystem
silent.

One check runs the other way round: an approval gate whose delivery channel
notifies nobody logs at ERROR by default and refuses only when a deployment asks
for it with ``approvals: {delivery: {required: true}}``. That gate is already
fail-closed by timeout, so the missing thing is a signal, not an enforcement
(#914).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SubsystemRequirement:
    """One subsystem the configuration asks for, and whether it is reachable.

    Attributes:
        subsystem: Stable name of the subsystem, e.g. ``approval_gate``.
        required_by: What in the configuration demands it, e.g.
            ``tools.approval_list on mcp_server:math``.
        reachable: Whether the runtime object that serves it is present.
        fail_closed: Whether an unreachable subsystem must refuse the boot
            rather than only log.
    """

    subsystem: str
    required_by: str
    reachable: bool
    fail_closed: bool = False


def _approval_gate_requirements(config: dict[str, Any], context: Any) -> list[SubsystemRequirement]:
    """Every configured tool access policy that gates a tool behind approval.

    Read off the resolver rather than the raw YAML on purpose: the resolver is
    what the executor consults, so this measures the policies that will actually
    be enforced, whatever loaded them (config file, hot reload, REST).

    Tool policies ONLY (#1043). Since #1028 the resolver also holds prompt and
    resource policies, and ``requires_approval()`` has one consumer -- the tool
    call path -- so a non-tool ``approval_list`` demanded a gate that could
    never hold anything, and refused the boot for it. That configuration is now
    refused at load (#1042); this keeps the check honest for a policy that
    arrives any other way.
    """
    from ...domain.services import get_tool_access_resolver

    gate = getattr(context, "approval_gate", None)
    reachable = gate is not None

    requirements: list[SubsystemRequirement] = []
    for scope, policy in get_tool_access_resolver().iter_registered_policies(kind="tool"):
        if not getattr(policy, "approval_list", ()):
            continue
        requirements.append(
            SubsystemRequirement(
                subsystem="approval_gate",
                required_by=f"tools.approval_list on {scope}",
                reachable=reachable,
                fail_closed=True,
            )
        )
    return requirements


def _approval_delivery_requirements(config: dict[str, Any], context: Any) -> list[SubsystemRequirement]:
    """A gate that holds calls, and a channel that tells nobody they are held.

    The gate is fail-closed: the wait elapses, ``ApprovalResult.expired()``
    denies, nothing executes unapproved. So this is *not* the fail-open shape
    the approval-gate check above guards, and it does not refuse the boot on its
    own -- refusing over a missing notification channel would turn a degraded
    notify path into an outage, and approvals remain resolvable over REST.

    What it is, is five minutes of every gated call hanging and then erroring,
    which from the outside is indistinguishable from a broken gateway. The
    remediation an operator reaches for under that pressure is emptying
    ``approval_list``: fail-closed in code, fail-open in the organisation
    (#914). An ERROR line naming the server and the channel is the difference
    between diagnosing that in a minute and reaching for the gate.

    A deployment that wants the stricter reading sets
    ``approvals.delivery.required: true`` and gets a refusal instead. Opt-in,
    because the honest default here is loud, not fatal.
    """
    from ...approvals.bootstrap import channel_reaches_a_human, configured_channel
    from ...domain.services import get_tool_access_resolver

    if getattr(context, "approval_gate", None) is None:
        return []  # Already the subject of _approval_gate_requirements.

    approvals_config = config.get("approvals")
    delivery_config = approvals_config.get("delivery", {}) if isinstance(approvals_config, dict) else {}
    required = bool(delivery_config.get("required", False)) if isinstance(delivery_config, dict) else False

    default_channel = configured_channel(config)
    reachable_channels: dict[str, bool] = {}

    requirements: list[SubsystemRequirement] = []
    for scope, policy in get_tool_access_resolver().iter_registered_policies(kind="tool"):
        if not getattr(policy, "approval_list", ()):
            continue

        channel = getattr(policy, "approval_channel", "") or default_channel
        if channel not in reachable_channels:
            reachable_channels[channel] = channel_reaches_a_human(channel)

        if reachable_channels[channel]:
            continue

        requirements.append(
            SubsystemRequirement(
                subsystem="approval_delivery",
                required_by=f"tools.approval_list on {scope} (channel {channel!r})",
                reachable=False,
                fail_closed=required,
            )
        )
    return requirements


def _task_relay_requirements(config: dict[str, Any], context: Any) -> list[SubsystemRequirement]:
    """The ADR-014 governed task relay, when the kill-switch says it should serve.

    This is #592 restated as a check: the relay was enabled by config, the SDK
    supported it, and ``ctx.governed_task_store`` was ``None`` because only the
    unused factory wired it.
    """
    from ..._sdk_compat import HAS_NATIVE_TASKS

    if not (HAS_NATIVE_TASKS and bool(config.get("relay_tasks_enabled", True))):
        return []

    return [
        SubsystemRequirement(
            subsystem="task_relay",
            required_by="relay_tasks_enabled",
            reachable=getattr(context, "governed_task_store", None) is not None,
        )
    ]


#: Every check the startup guard runs. Adding a subsystem means adding a
#: function here -- the enforcement, logging and refusal behaviour is shared.
REQUIREMENT_CHECKS = (
    _approval_gate_requirements,
    _approval_delivery_requirements,
    _task_relay_requirements,
)


def collect_subsystem_requirements(config: dict[str, Any], context: Any) -> list[SubsystemRequirement]:
    """Return every subsystem this configuration demands, reachable or not."""
    requirements: list[SubsystemRequirement] = []
    for check in REQUIREMENT_CHECKS:
        try:
            requirements.extend(check(config, context))
        except Exception:  # noqa: BLE001 -- a broken check must not decide the boot
            logger.warning("subsystem_reachability_check_failed", check=check.__name__, exc_info=True)
    return requirements


def check_subsystem_reachability(config: dict[str, Any], context: Any) -> list[SubsystemRequirement]:
    """Return only the demanded subsystems that are unreachable."""
    return [req for req in collect_subsystem_requirements(config, context) if not req.reachable]


def enforce_subsystem_reachability(config: dict[str, Any], context: Any) -> list[SubsystemRequirement]:
    """Log every unreachable subsystem, and refuse the boot on the fail-closed ones.

    Args:
        config: Full application configuration dictionary.
        context: The object the runtime reads its subsystems off -- the
            application context.

    Returns:
        The unreachable requirements, so callers can assert on them.

    Raises:
        ConfigurationError: When a fail-closed subsystem is unreachable and
            ``startup_checks.enforce`` has not been turned off.
    """
    unreachable = check_subsystem_reachability(config, context)
    if not unreachable:
        return []

    for req in unreachable:
        logger.error(
            "subsystem_configured_but_unreachable",
            subsystem=req.subsystem,
            required_by=req.required_by,
            fail_closed=req.fail_closed,
        )

    startup_checks = config.get("startup_checks")
    enforce = True
    if isinstance(startup_checks, dict):
        enforce = bool(startup_checks.get("enforce", True))

    blocking = [req for req in unreachable if req.fail_closed]
    if blocking and enforce:
        from ...domain.exceptions import ConfigurationError

        detail = "; ".join(f"{req.subsystem} required by {req.required_by}" for req in blocking)
        raise ConfigurationError(
            f"Configured subsystem is not reachable on this server: {detail}. "
            "The configuration asks for enforcement this process cannot perform. "
            "Fix the wiring, remove the configuration, or set startup_checks.enforce: false "
            "to downgrade this to an error log."
        )

    return unreachable
