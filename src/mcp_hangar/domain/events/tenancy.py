"""Which tenant a domain event belongs to."""

from collections.abc import Mapping


def event_tenant_id(event: object) -> str | None:
    """The one tenant *event* belongs to; None when it names none, or two.

    Events carry their tenant in one of two places:

    * a ``tenant_id`` attribute -- authentication, tenancy and quota, task,
      cost, approval, withdrawal and digest-pin events;
    * ``identity_context["tenant_id"]`` -- the caller's identity on invocation
      and enforcement events.

    A reader confined to one tenant has to treat None as "not yours". An event
    that names no tenant (fleet lifecycle, health, discovery, an all-tenants
    withdrawal) is fleet business. An event that names two different tenants
    cannot be attributed to either. Only a reader whose grant reaches the whole
    fleet sees those.
    """
    tenants: set[str] = set()
    direct = getattr(event, "tenant_id", None)
    if isinstance(direct, str) and direct:
        tenants.add(direct)
    identity = getattr(event, "identity_context", None)
    if isinstance(identity, Mapping):
        nested = identity.get("tenant_id")
        if isinstance(nested, str) and nested:
            tenants.add(nested)
    return tenants.pop() if len(tenants) == 1 else None
