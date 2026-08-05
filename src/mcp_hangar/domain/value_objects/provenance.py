"""Who asked for a server to exist.

Not the same question as `source`, which is a free-text label an operator reads
(`api`, `cli`, `discovery:docker`). Provenance is what security policy is allowed
to branch on, so it must be established by the construction path and never be
settable by whoever is on the other end of it. A string prefix cannot carry that
guarantee: the moment a policy says `source.startswith("discovery:")`, anyone
who can reach a route that forwards `source` has the keys to it.
"""

from __future__ import annotations

from enum import Enum


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
