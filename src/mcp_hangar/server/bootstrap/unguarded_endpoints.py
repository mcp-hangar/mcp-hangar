"""Say which remote upstreams the SSRF policy does not cover (#903).

`enforce_ssrf` is set in exactly one place -- the command handler behind the
REST API and discovery -- so a `remote` server declared in `config.yaml` gets
neither half of the protection:

* no `validate_no_ssrf` at registration, so an endpoint the API answers 400
  `ssrf_blocked` for is accepted from the file without comment;
* no `_SsrfGuardedTransport`, so the connect-time re-resolution and IP pinning
  added in 2.5.0 -- the part that closes DNS rebinding, and the part that has to
  run on *every* request -- never runs for it.

That exclusion is deliberate and is argued in `http_client.HttpClientConfig`:
the operator's file is trusted, a config-file upstream on a private address is
the normal case, and applying the strict policy there would refuse endpoints an
operator meant. The rationale holds. What did not hold is that the decision was
invisible: an operator who moved an upstream out of the API and into the file
lost two controls and nothing said so.

So this warns rather than refuses, and it warns about the endpoint in front of
it rather than about the class. Once per boot, no throttling -- the set is fixed
by the file.
"""

from __future__ import annotations

from typing import Any

from ...domain.security.ssrf import endpoint_is_a_literal_the_strict_policy_refuses
from ...logging_config import get_logger

logger = get_logger(__name__)

#: The mode whose endpoint is dialled over HTTP, and so the only one the SSRF
#: policy has an opinion about.
_REMOTE_MODE = "remote"


def _config_file_remote_endpoints(config: dict[str, Any]) -> list[tuple[str, str]]:
    """Every `(server_id, endpoint)` declared as `mode: remote` in the file.

    Servers registered through the API never appear in this document, which is
    what makes reading it the right way to ask the question: the population here
    is exactly the population `enforce_ssrf` is off for.
    """
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return []
    found: list[tuple[str, str]] = []
    for server_id, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        if str(spec.get("mode", "")).strip().lower() != _REMOTE_MODE:
            continue
        endpoint = spec.get("endpoint")
        if isinstance(endpoint, str) and endpoint:
            found.append((str(server_id), endpoint))
    return found


def warn_about_endpoints_the_ssrf_policy_does_not_cover(config: dict[str, Any] | None = None) -> int:
    """Log one line per config-file `remote` upstream, naming what it does not get.

    Two messages, because the two situations differ in how much was given up. An
    endpoint written as a private or metadata-adjacent literal is one the API
    would have refused outright, so the operator is told that in those words. Any
    other endpoint -- a public name, most often -- still loses the connect-time
    re-check, which is the DNS-rebinding half and the one an operator is least
    likely to have thought about.

    Args:
        config: Full configuration document.

    Returns:
        How many upstreams were warned about, so a caller (and a test) can tell
        "nothing to say" from "said nothing".
    """
    endpoints = _config_file_remote_endpoints(config or {})
    for server_id, endpoint in endpoints:
        if endpoint_is_a_literal_the_strict_policy_refuses(endpoint):
            logger.warning(
                "ssrf_policy_not_applied_to_config_file_endpoint",
                mcp_server_id=server_id,
                endpoint=endpoint,
                detail=(
                    "this endpoint resolves to a private or metadata-adjacent address and would be "
                    "refused with `ssrf_blocked` if it were registered through the API. Declared in "
                    "config.yaml it is accepted, and it also skips the connect-time re-check that "
                    "closes DNS rebinding -- neither half of the SSRF policy applies to it. That is "
                    "deliberate: the configuration file is trusted, and a private upstream declared "
                    "there is usually meant. Register it through the API instead if it should be "
                    "checked."
                ),
            )
        else:
            logger.warning(
                "ssrf_policy_not_applied_to_config_file_endpoint",
                mcp_server_id=server_id,
                endpoint=endpoint,
                detail=(
                    "a remote upstream declared in config.yaml gets neither half of the SSRF policy: "
                    "no validation at registration, and no connect-time re-resolution, so if this "
                    "hostname is later re-pointed at an internal address every call follows it. "
                    "Endpoints registered through the API are re-checked on every connection. That "
                    "difference is deliberate -- the file is trusted -- but it is a difference."
                ),
            )
    return len(endpoints)
