"""UI resource (MCP Apps / SEP-1865) policy value objects.

MCP Apps lets a tool return a ``ui://`` resource that a client renders inside a
webview / sandboxed iframe. That makes ``ui://`` content an untrusted boundary:
an XSS / data-exfiltration surface. This module provides the immutable,
fail-closed policy primitives used to gate ``ui://`` resources:

- :data:`UI_SCHEME`: the ``ui://`` URI scheme these rules apply to.
- :data:`DEFAULT_UI_CSP`: a restrictive Content-Security-Policy applied to any
  allowed ``ui://`` resource.
- :func:`is_ui_scheme`: pure predicate -- does a URI target the ``ui://`` scheme.
- :func:`matches_ui_allowlist`: pure matcher -- is a ``ui://`` URI permitted by a
  given allowlist (exact URI or origin / prefix match, optional trailing ``*``).
- :class:`UiResourcePolicy`: a per-tenant allowlist + CSP. **An empty allowlist
  denies every ``ui://`` resource** (fail-closed default).

There is no ``ui://`` resource relay in Hangar today; these primitives back the
dormant-but-ready guard (:mod:`mcp_hangar.domain.services.ui_resource_guard`)
that activates when ``ui://`` resources begin flowing through the proxy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The URI scheme MCP Apps uses for renderable UI resources.
UI_SCHEME = "ui://"

# Restrictive default Content-Security-Policy for an allowed ``ui://`` resource.
#
# Fail-closed posture: deny everything by default, then re-grant only what a
# self-contained, sandboxed webview view legitimately needs. In particular:
#   - ``default-src 'none'``     -- nothing loads unless explicitly re-granted.
#   - ``connect-src 'none'``     -- no outbound fetch/XHR/WebSocket exfiltration.
#   - ``frame-ancestors 'none'``-- the view cannot be framed / clickjacked.
#   - ``base-uri 'none'`` + ``form-action 'none'`` -- no base-tag hijack, no
#     form-based data exfiltration.
#   - ``sandbox`` with only ``allow-scripts`` -- no same-origin, no top-level
#     navigation, no popups, no form submission.
# Scripts/styles/images are limited to ``'self'`` (the resource's own origin)
# so declared assets render without opening arbitrary remote origins.
DEFAULT_UI_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "sandbox allow-scripts"
)


def is_ui_scheme(uri: str) -> bool:
    """Return True if ``uri`` targets the ``ui://`` MCP Apps scheme.

    Case-insensitive on the scheme (URI schemes are case-insensitive per
    RFC 3986). Non-string / empty input returns False.
    """
    if not uri or not isinstance(uri, str):
        return False
    return uri.lower().startswith(UI_SCHEME)


def matches_ui_allowlist(uri: str, allowlist: frozenset[str]) -> bool:
    """Return True if a ``ui://`` ``uri`` is permitted by ``allowlist``.

    Matching is fail-closed: an empty allowlist matches nothing, so every
    ``ui://`` resource is denied by default. Each allowlist entry permits a URI
    when:

    - the entry equals the URI exactly, or
    - the entry ends with ``*`` and the URI starts with the entry's prefix
      (an explicit wildcard, e.g. ``ui://reports/*``), or
    - the entry ends with ``/`` and the URI starts with the entry (an origin /
      path-prefix grant, e.g. ``ui://reports/``).

    Only the ``ui://`` scheme is matched here; callers must gate on
    :func:`is_ui_scheme` first. Comparison is case-sensitive on the path (paths
    are case-sensitive) but the ``ui://`` scheme prefix comparison tolerates
    case via normalization.
    """
    if not is_ui_scheme(uri) or not allowlist:
        return False

    # Normalize only the scheme prefix; keep the authority/path case-sensitive.
    normalized = UI_SCHEME + uri[len(UI_SCHEME) :]

    for raw_entry in allowlist:
        if not is_ui_scheme(raw_entry):
            # Non-ui:// entries never grant a ui:// resource (fail-closed).
            continue
        entry = UI_SCHEME + raw_entry[len(UI_SCHEME) :]
        if entry.endswith("*"):
            if normalized.startswith(entry[:-1]):
                return True
        elif entry.endswith("/"):
            if normalized.startswith(entry):
                return True
        elif normalized == entry:
            return True
    return False


@dataclass(frozen=True)
class UiResourcePolicy:
    """Per-tenant policy governing ``ui://`` (MCP Apps) resources.

    Attributes:
        allowlist: Set of permitted ``ui://`` URIs / origins. **Empty (the
            default) denies every ``ui://`` resource** -- fail-closed.
        csp: Content-Security-Policy attached to an allowed ``ui://`` resource.
            Defaults to the restrictive :data:`DEFAULT_UI_CSP`.
        require_consent: Whether an allowed ``ui://`` resource additionally
            requires a passed consent gate before delivery. Defaults to True and
            is intentionally not configurable to False through normal config
            loading -- consent is mandated (fail-closed) even for allowlisted
            resources.
    """

    allowlist: frozenset[str] = field(default_factory=frozenset)
    csp: str = DEFAULT_UI_CSP
    require_consent: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.allowlist, frozenset):
            raise TypeError("allowlist must be a frozenset of str")
        if not isinstance(self.csp, str) or not self.csp:
            raise ValueError("csp must be a non-empty string")

    def is_allowed(self, uri: str) -> bool:
        """Return True if ``uri`` is a ``ui://`` resource this policy permits.

        Fail-closed: returns False for any ``ui://`` URI not matched by the
        allowlist (including when the allowlist is empty).
        """
        return matches_ui_allowlist(uri, self.allowlist)
