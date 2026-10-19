"""What the operator lets a server loaded from the registry have.

A registry entry is written by whoever published it. It names the server, the
package to run and the environment variables it wants, and none of that is the
gateway's decision. This is: the `hot_loading` keys of `config.yaml` below,
checked by `parse_hot_load_trust` and put in force by `set_hot_load_trust`,
at startup and on every reload. The load handler asks `hot_load_trust()` once
per load, so a reload that narrows a list applies to the next load.

::

    hot_loading:
      trusted_namespaces: ["io.github.acme/"]
      allow_force_unverified: false
      secrets:
        allow:
          "io.github.acme/weather": [WEATHER_API_KEY]

`trusted_namespaces` is the only source of "verified". The registry marks every
entry it publishes `status: active`, so that status admits anyone who can
publish; a namespace is what the registry authenticates the publisher of. An
entry is verified when its exact registry name starts with a listed namespace,
compared case-sensitively and whole segment by whole segment: each namespace
ends in `/` (one is added when it is left out), so `io.github.acme/` matches
`io.github.acme/x` and `io.github.acme/tools/x`, and not `io.github.acmeevil/x`
or `io.github.acme` itself. A sub-namespace is not implied by its parent's
reverse-DNS prefix: `io.github.acme/` does not trust `io.github.acme.labs/x`.

Keys are exact registry names, never the sanitized local id: sanitizing is
lossy (`io.github.acme/x` and `io-github-acme/x` both become
`io-github-acme-x`), so a key on the sanitized form would hand one server's
secrets to anyone who publishes a colliding name. Two keys that differ only in
case are refused, because their secret files would share a directory on a
case-insensitive filesystem.

A server loaded from the registry does not inherit the gateway's environment:
Hangar hands it `BASE_INHERITED_ENV`, the names in `inherit_env` and its bound
secrets. A statically configured server is unaffected. What Hangar hands the
server is bounded; a hot-loaded server is code running as the gateway's user,
so `trusted_namespaces` is the boundary. It can read whatever that user can --
`~/.config/mcp-hangar/secrets/`, `~/.aws/credentials` -- on its own.

`allow_force_unverified` decides whether the `force_unverified` argument of
`hangar_load` does anything. The argument is the caller's -- an agent's, which
a registry entry's own text can steer -- so by default it is ignored, and an
unverified entry is refused whatever the call says.

An absent section trusts nothing and binds nothing: every entry is unverified,
and no secret is delivered to any hot-loaded server.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .input_validator import is_env_var_name

_SECRETS_KEYS = frozenset({"allow"})

#: What a server loaded from the registry takes from the gateway's environment
#: whatever the configuration says: what a process, `uvx` and `npx` need to
#: find binaries, a home, a locale, a timezone and a temporary directory, and
#: the Windows equivalents. Nothing that holds a credential. Proxies, CA
#: bundles and `UV_*`/`npm_config_*` settings are left out and can be added
#: with `hot_loading.inherit_env`.
BASE_INHERITED_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
        "TMP",
        "TEMP",
        # Windows: the system, the shell, executable lookup, and where uv and
        # npm keep their caches and configuration.
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
    }
)


@dataclass(frozen=True)
class HotLoadTrust:
    """The operator's decisions about hot-loaded servers. The default decides nothing, so allows nothing.

    Attributes:
        trusted_namespaces: Registry name prefixes, each ending in `/`, whose
            entries are verified.
        allow_force_unverified: Whether a caller's `force_unverified` may load
            an unverified entry. False: the argument is ignored.
        inherit_env: Names, beyond `BASE_INHERITED_ENV`, that a hot-loaded
            server takes from the gateway's environment.
        secrets_allow: Exact registry name -> the variable names that server may receive.
    """

    trusted_namespaces: tuple[str, ...] = ()
    allow_force_unverified: bool = False
    inherit_env: frozenset[str] = frozenset()
    secrets_allow: Mapping[str, frozenset[str]] = field(default_factory=lambda: MappingProxyType({}))

    def is_trusted(self, server_name: str) -> bool:
        """Whether `server_name` lies under a trusted namespace: a listed prefix and at least one more character."""
        return any(server_name.startswith(ns) and len(server_name) > len(ns) for ns in self.trusted_namespaces)

    def inherited_env_names(self) -> frozenset[str]:
        """Every name a hot-loaded server takes from the gateway's environment."""
        return BASE_INHERITED_ENV | self.inherit_env

    def allowed_secrets(self, server_name: str) -> frozenset[str]:
        """The variables `server_name` may receive; empty when the operator named none."""
        return self.secrets_allow.get(server_name, frozenset())


def parse_hot_load_trust(section: object) -> HotLoadTrust:
    """Check the `hot_loading` section's trust keys and return them, or refuse them.

    Fails closed: a value of the wrong shape, or a variable name that could not
    be an environment variable, is a ValueError naming the key, not an entry
    skipped.
    """
    if section is None:
        return HotLoadTrust()
    if not isinstance(section, dict):
        raise ValueError(f"hot_loading must be a mapping, got {type(section).__name__}")
    return HotLoadTrust(
        trusted_namespaces=_parse_trusted_namespaces(section.get("trusted_namespaces")),
        allow_force_unverified=_parse_allow_force_unverified(section.get("allow_force_unverified")),
        inherit_env=_parse_inherit_env(section.get("inherit_env")),
        secrets_allow=_parse_secrets_allow(section.get("secrets")),
    )


def _parse_inherit_env(values: object) -> frozenset[str]:
    """Environment variable names, or absent."""
    if values is None:
        return frozenset()
    if not isinstance(values, list):
        raise ValueError(f"hot_loading.inherit_env must be a list of variable names, got {type(values).__name__}")
    for value in values:
        if not is_env_var_name(value):
            raise ValueError(f"hot_loading.inherit_env entry {value!r} is not an environment variable name")
    return frozenset(values)


def _parse_allow_force_unverified(value: object) -> bool:
    """A bool, or absent. `"false"` is a string, and a non-empty string is truthy: refused, not read."""
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError(f"hot_loading.allow_force_unverified must be true or false, got {value!r}")
    return value


def _parse_trusted_namespaces(values: object) -> tuple[str, ...]:
    """The namespaces, each ending in `/`. Refuses what would trust more than it names."""
    if values is None:
        return ()
    if not isinstance(values, list):
        raise ValueError(f"hot_loading.trusted_namespaces must be a list of namespaces, got {type(values).__name__}")
    namespaces: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"hot_loading.trusted_namespaces entry {value!r} is not a string")
        namespace = value if value.endswith("/") else f"{value}/"
        head = namespace[:-1]
        if not head or head != head.strip() or any(c.isspace() for c in head) or "*" in head or "//" in namespace:
            raise ValueError(
                f"hot_loading.trusted_namespaces entry {value!r} is not a namespace: write the registry "
                "name prefix to trust, such as 'io.github.acme/'. Nothing is matched by pattern, and an "
                "empty prefix would trust every entry"
            )
        namespaces.append(namespace)
    return tuple(namespaces)


def _parse_secrets_allow(secrets: object) -> Mapping[str, frozenset[str]]:
    if secrets is None:
        return MappingProxyType({})
    if not isinstance(secrets, dict):
        raise ValueError(f"hot_loading.secrets must be a mapping, got {type(secrets).__name__}")
    unknown = sorted(set(secrets) - _SECRETS_KEYS, key=str)
    if unknown:
        raise ValueError(f"hot_loading.secrets has unknown key(s) {unknown}; allowed keys: {sorted(_SECRETS_KEYS)}")
    allow = secrets.get("allow")
    if allow is None:
        return MappingProxyType({})
    if not isinstance(allow, dict):
        raise ValueError(
            "hot_loading.secrets.allow must map a server's registry name to a list of variable names, "
            f"got {type(allow).__name__}"
        )
    parsed: dict[str, frozenset[str]] = {}
    folded: dict[str, str] = {}
    for server, names in allow.items():
        if not isinstance(server, str) or not server.strip():
            raise ValueError(f"hot_loading.secrets.allow key {server!r} is not a server's registry name")
        if not isinstance(names, list):
            raise ValueError(
                f"hot_loading.secrets.allow[{server!r}] must be a list of variable names, got {type(names).__name__}"
            )
        for name in names:
            if not is_env_var_name(name):
                raise ValueError(
                    f"hot_loading.secrets.allow[{server!r}] entry {name!r} is not an environment variable name"
                )
        # A server's secret files live under its registry name, and on a
        # case-insensitive filesystem two keys that differ only in case would
        # read one directory.
        clash = folded.setdefault(server.casefold(), server)
        if clash != server:
            raise ValueError(
                f"hot_loading.secrets.allow keys {clash!r} and {server!r} differ only in case; their secret "
                "files would share one directory on a case-insensitive filesystem. Keep one of them"
            )
        parsed[server] = frozenset(names)
    return MappingProxyType(parsed)


#: The trust in force. Replaced whole by `set_hot_load_trust`.
_hot_load_trust = HotLoadTrust()


def set_hot_load_trust(trust: HotLoadTrust) -> None:
    """Replace the trust in force. Takes `parse_hot_load_trust`'s result."""
    global _hot_load_trust
    _hot_load_trust = trust


def hot_load_trust() -> HotLoadTrust:
    """The trust in force."""
    return _hot_load_trust
