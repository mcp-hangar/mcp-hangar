"""`header_exposure`: what an upstream may oblige a client to put in a header.

SEP-2243 lets a tool annotate an `inputSchema` property with `x-mcp-header`,
and a conforming client then sends that argument's value as an HTTP header
instead of in the body. The spec's only defence against annotating a secret is
a SHOULD NOT. An upstream that annotates `api_key` obliges every conforming
client to put the key in a header, where every intermediary on the path can
read it, and no client-side rule stops it.

#1056 validates the *syntax* of those annotations. This is the semantics: which
parameter names an operator is willing to have exposed that way.

The annotation is never stripped. The digest is JCS over
`{name, description, inputSchema, outputSchema}`, so editing the schema would
move it and read as upstream drift to every pin -- the tool is withheld (or
merely reported) instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

#: What to do about a denied exposure. ``warn`` is the default so that adopting
#: the feature does not change anyone's surface.
ON_VIOLATION_ACTIONS: tuple[str, ...] = ("warn", "withdraw", "refuse_boot")

X_MCP_HEADER = "x-mcp-header"


def _annotated_properties(input_schema: Any) -> Iterator[tuple[str, str]]:
    """Yield ``(property path, x-mcp-header token)`` for every annotation.

    Only pure ``properties`` chains are walked. That is the only position where
    a valid annotation can sit, and a tool carrying one anywhere else is
    already gone by the time this runs (#1056).
    """
    if not isinstance(input_schema, Mapping):
        return
    stack: list[tuple[tuple[str, ...], Mapping[str, Any]]] = [((), input_schema)]
    while stack:
        path, schema = stack.pop()
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            continue
        for name, subschema in properties.items():
            if not isinstance(subschema, Mapping):
                continue
            here = (*path, str(name))
            token = subschema.get(X_MCP_HEADER)
            if isinstance(token, str):
                yield ".".join(here), token
            stack.append((here, subschema))


@dataclass(frozen=True)
class HeaderExposurePolicy:
    """One ``header_exposure:`` block, resolved for one mcp_server or group."""

    deny_annotated: tuple[str, ...] = ()
    on_violation: str = "warn"

    def __bool__(self) -> bool:
        return bool(self.deny_annotated)

    @classmethod
    def from_config(cls, block: Any) -> HeaderExposurePolicy | None:
        """Parse a ``header_exposure:`` block. ``None`` when absent.

        Raises ``ValueError`` on an unknown ``on_violation``. A typo there would
        otherwise resolve to the default and report a policy as enforcing while
        the action the author asked for never happens -- the same failure this
        package refuses for unknown secret-pattern groups.
        """
        if block is None:
            return None
        if not isinstance(block, dict):
            raise ValueError("header_exposure must be a mapping")

        raw = block.get("deny_annotated") or []
        if not isinstance(raw, list) or not all(isinstance(g, str) for g in raw):
            raise ValueError("header_exposure.deny_annotated must be a list of strings")

        action = block.get("on_violation", "warn")
        if action not in ON_VIOLATION_ACTIONS:
            raise ValueError(f"invalid header_exposure.on_violation {action!r} (want {'|'.join(ON_VIOLATION_ACTIONS)})")

        return cls(deny_annotated=tuple(raw), on_violation=action)

    def violation(self, input_schema: Any) -> str | None:
        """The first denied exposure in *input_schema*, or ``None``.

        Both the annotation **token** and the **property path** are matched: an
        upstream can name the property `api_key` and the header `X-Key`, or the
        other way round, and either spelling is the same exposure.
        """
        if not self.deny_annotated:
            return None
        for path, token in _annotated_properties(input_schema):
            for glob in self.deny_annotated:
                lowered = glob.lower()
                if fnmatchcase(path.lower(), lowered) or fnmatchcase(token.lower(), lowered):
                    return (
                        f"property {path!r} is annotated {token!r}, so a conforming client "
                        f"sends it as a header; matched deny_annotated {glob!r}"
                    )
        return None


# Config overlay, keyed by mcp_server or group id, mirroring how the withdrawal
# and pin overlays live on the projection registry: populated at config-load
# time, cleared before a reload so that deleting the block restores the tools.
_POLICIES: dict[str, HeaderExposurePolicy] = {}


def set_header_exposure_policy(scope_id: str, policy: HeaderExposurePolicy) -> None:
    """Register one scope's parsed block."""
    _POLICIES[scope_id] = policy


def get_header_exposure_policy(scope_id: str) -> HeaderExposurePolicy | None:
    """The policy declared for *scope_id*, or ``None``."""
    return _POLICIES.get(scope_id)


def clear_header_exposure_policies() -> None:
    """Drop every registered block."""
    _POLICIES.clear()


def adopt_header_exposure_policies(policies: dict[str, HeaderExposurePolicy], *, replace: bool) -> None:
    """Take a configuration's blocks in one step (#1424).

    One rebinding, so a concurrent read sees the previous blocks or the new ones,
    never the empty overlay a reload used to leave between clearing it and
    registering it again. With *replace*, which is what a reload asks for,
    deleting a block from the file restores the tools it withheld; without it,
    which is what a first load does, the blocks are added to what is there.
    """
    global _POLICIES
    _POLICIES = dict(policies) if replace else {**_POLICIES, **policies}


#: Which groups own a server: registered by the delivery layer, which holds the
#: groups (#2011). The client reads it to find the blocks that govern a server,
#: as the flat projection and the batch gates do; None until registered.
_OWNERS: Callable[[str], tuple[str, ...]] | None = None


def set_group_owners(owners: Callable[[str], tuple[str, ...]]) -> None:
    """Register how to find the ids of every group that owns a server."""
    global _OWNERS
    _OWNERS = owners


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


def mirror_refusal(mcp_server: str | None, input_schema: Any) -> str | None:
    """Why Hangar, as a client, must not mirror this schema's ``x-mcp-header`` parameters, or ``None`` (#2011).

    The rule the batch gates and the flat projection apply (#2253), judged on
    the schema the headers would be built from: a block that withholds rather
    than warns, and that *input_schema* violates. Fails closed: with a block
    that withholds anywhere, a server with no id, or one whose groups cannot be
    read, is refused rather than mirrored.
    """
    if not any(policy and policy.on_violation != "warn" for policy in _POLICIES.values()):
        return None
    if not mcp_server:
        return "server_unknown"
    owners = _OWNERS(mcp_server) if _OWNERS is not None else None
    if owners is None and not get_header_exposure_policy(mcp_server):
        return "owners_unknown"
    for policy in exposure_policies(mcp_server, owners or ()):
        if policy.on_violation != "warn" and policy.violation(input_schema) is not None:
            return f"header_exposure_{policy.on_violation}"
    return None
