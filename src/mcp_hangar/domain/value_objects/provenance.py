"""Who asked for a server to exist.

Not the same question as `source`, which is a free-text label an operator reads
(`api`, `cli`, `discovery:docker`). Provenance is what security policy is allowed
to branch on, so it must be established by the construction path and never be
settable by whoever is on the other end of it. A string prefix cannot carry that
guarantee: the moment a policy says `source.startswith("discovery:")`, anyone
who can reach a route that forwards `source` has the keys to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Provenance(Enum):
    """How a registration reached the command bus.

    HUMAN: someone asked -- the REST API, the CLI, a config file. Untrusted by
        default, because the endpoint is whatever they typed.

    DISCOVERY: a discovery source reported something the infrastructure already
        runs. Trusted *only* in a narrow, checkable sense: the endpoint has to be
        an address the container runtime itself reports for that container or
        pod. Provenance alone buys nothing.
    """

    HUMAN = "human"
    DISCOVERY = "discovery"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class DiscoveryOrigin:
    """Which discovery source registered a server, and the definition it reported (#2106).

    Recorded with a discovered server's row so that discovery can take the
    server back after a restart: its source, to know whose mode governs it and
    which source can lose it; its fingerprint, to tell an unchanged definition
    from a changed one; and the namespace it came from, the one part of the
    source's metadata a partly failed scan is judged by (#2089). Nothing else
    of the metadata is kept: annotations can carry far more than this needs.

    Not a security input. Provenance is what policy branches on; this only
    says where to hand a DISCOVERY server back to.
    """

    source_type: str
    fingerprint: str
    namespace: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """The JSON form stored with the row."""
        return {"source_type": self.source_type, "fingerprint": self.fingerprint, "namespace": self.namespace}

    @classmethod
    def from_dict(cls, data: Any) -> DiscoveryOrigin | None:
        """Read the stored form back; None for a missing or malformed one.

        A row this cannot read is treated as one that never recorded an
        origin, which is how every row written before the field reads.
        """
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            return None
        source_type, fingerprint, namespace = data.get("source_type"), data.get("fingerprint"), data.get("namespace")
        if not isinstance(source_type, str) or not source_type or not isinstance(fingerprint, str) or not fingerprint:
            return None
        return cls(source_type, fingerprint, namespace if isinstance(namespace, str) and namespace else None)
