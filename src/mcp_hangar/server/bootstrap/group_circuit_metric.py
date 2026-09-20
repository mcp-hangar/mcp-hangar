"""Each group's circuit, as this replica sees it, on ``/metrics`` (#1357).

A group tells its listener about every transition of its breaker. This module
is that listener. The two places a group starts being served attach it: loading
the config at bootstrap, and ``GroupCreated`` from the group API.
``MetricsEventHandler`` drops a deleted group's series.

The listener writes directly, not through the bus. It was built that way
because nothing drained a group's events on the call path or in the health
checks, so its circuit events never reached the bus from either; #1410 fixed
that, and they do now. It stays that way because the gauge is replica-local on
purpose (#1380) and the callback is the only source that cannot be anything
else: it fires for every transition of this pod's breaker, including ones no
domain event is recorded for. The events say the same thing and are published
locally for the same reason -- putting breaker transitions on the shared log is
the design #1358 defers.
"""

from __future__ import annotations

from ... import metrics as prometheus_metrics
from ...domain.events import DomainEvent
from ...domain.model.mcp_server_group import GroupCreated, McpServerGroup
from .composition import GROUPS


def observe_group_circuit(group: McpServerGroup) -> None:
    """Keep ``mcp_hangar_group_circuit_open`` for ``group`` in step with its breaker.

    Writes the state now, so the series exists before the first transition,
    and after every transition. Writes only while ``group`` is the group this
    replica serves under its id. Otherwise a late transition of a deleted or
    replaced group would put back the series its removal dropped. That narrows
    the race rather than closing it: a write that passed the check just as the
    group was being deleted can still land after the removal.
    """
    group_id = group.id

    def write(is_open: bool) -> None:
        if GROUPS.get(group_id) is group:
            prometheus_metrics.set_group_circuit_open(group_id, is_open)

    group.observe_circuit(write)


def observe_created_group(event: DomainEvent) -> None:
    """A group created through the API goes on ``/metrics`` too.

    A ``LOCAL_VIEW`` handler: it reads this replica's ``GROUPS``, not the event.
    """
    if not isinstance(event, GroupCreated):
        return
    group = GROUPS.get(event.group_id)
    if group is not None:
        observe_group_circuit(group)
