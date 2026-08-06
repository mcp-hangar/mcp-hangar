"""Which process produced an event.

Every event carries the identity of the instance that produced it (`produced_by`
on `DomainEvent`). With one replica that is an audit convenience. With three it
is a correctness requirement: a replica publishes an event to its own handlers
*and* appends it to the shared log, and it will later tail that log. Without a
producer on the row it cannot tell its own append from a peer's, so it either
delivers everything twice or skips a peer's event -- and it cannot know which.

**The identity is minted, not configured.** A label may be supplied (the pod
name, typically, via the downward API) and it is only a prefix; the identity
always ends in a per-process suffix. Configuration that names the identity
directly has one bad outcome that nothing catches: three replicas rolled from one
ConfigMap share an id, each treats its peers' events as its own, and the tail
goes silent while every health check stays green. Minting makes uniqueness
structural rather than a thing the operator has to get right.

`UNKNOWN_PRODUCER` is what a row written before this existed deserializes to. It
is deliberately not a valid minted id, so "unknown" can never compare equal to a
live instance and a replayed row is never mistaken for one of ours.
"""

from __future__ import annotations

import uuid

#: Producer of an event stored before events carried one. Never minted, so it
#: never equals a live instance id -- an old row therefore reads as "not mine",
#: which is the safe direction: it gets delivered rather than silently skipped.
UNKNOWN_PRODUCER = "unknown"

_DEFAULT_LABEL = "hangar"

_instance_id: str | None = None


def set_instance_id(label: str | None = None) -> str:
    """Mint this process's instance identity and make it the current one.

    Called once during bootstrap, before anything can publish. The suffix is
    minted here rather than taken from `label`, so two processes given the same
    label are still two instances.

    Args:
        label: Human-readable prefix -- a pod name, a container name. Only for
            recognising the instance in an audit trail; it carries no meaning.

    Returns:
        The minted identity.
    """
    global _instance_id
    base = (label or "").strip() or _DEFAULT_LABEL
    _instance_id = f"{base}-{uuid.uuid4().hex[:8]}"
    return _instance_id


def current_instance_id() -> str:
    """This process's instance identity, minting one if bootstrap did not.

    An embedded or test use that never bootstraps still gets a stable, unique
    id rather than a placeholder shared with every other process.
    """
    if _instance_id is None:
        return set_instance_id(None)
    return _instance_id
