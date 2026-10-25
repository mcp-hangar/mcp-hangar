"""Over the served front door: a bound OIDC issuer cannot name another issuer's tenant.

Two trusted issuers, each with its own RS256 key and ``allowed_tenants``. The
auth config goes through ``parse_auth_config`` and ``bootstrap_auth``; only the
JWKS fetch is stubbed. Tenant A sees ``read_item``, tenant B sees
``write_item``. Before the binding, issuer B's token naming tenant A was served
tenant A's catalogue.

Naming: neutral placeholders only (issuer-a/issuer-b.example.com, tenant:a, tenant:b).
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mcp_hangar.auth.bootstrap import AuthComponents, bootstrap_auth
from mcp_hangar.auth.config import parse_auth_config
from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator

from ._front_door_harness import TENANT_A, TENANT_B, FrontDoor, front_door, jsonrpc

pytestmark = pytest.mark.security

_ISSUER_A = "https://issuer-a.example.com"
_ISSUER_B = "https://issuer-b.example.com"
_AUDIENCE = "https://gateway.example.com"
_KEYS = {issuer: rsa.generate_private_key(public_exponent=65537, key_size=2048) for issuer in (_ISSUER_A, _ISSUER_B)}


class _SigningKey:
    def __init__(self, key: Any):
        self.key = key


class _StubJwksClient:
    """The JWKS fetch, and nothing else: answers with the issuer's public key."""

    def __init__(self, issuer: str):
        self._issuer = issuer

    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        return _SigningKey(_KEYS[self._issuer].public_key())


def _auth(**bindings: list[str]) -> AuthComponents:
    entries = []
    for issuer, name in ((_ISSUER_A, "a"), (_ISSUER_B, "b")):
        entry: dict[str, Any] = {"issuer": issuer, "audience": _AUDIENCE, "jwks_uri": f"{issuer}/jwks"}
        if name in bindings:
            entry["allowed_tenants"] = bindings[name]
        entries.append(entry)
    components = bootstrap_auth(
        parse_auth_config(
            {
                "enabled": True,
                "api_key": {"enabled": False},
                "rate_limit": {"enabled": False},
                "oidc": {"enabled": True, "issuers": entries},
            }
        )
    )
    for authenticator in components.authn_middleware._authenticators:
        if isinstance(authenticator, JWTAuthenticator):
            for validator in authenticator._validator._validators.values():
                validator._jwks_client = _StubJwksClient(validator._config.issuer)
    return components


def _bearer(issuer: str, **claims: Any) -> dict[str, str]:
    now = int(time.time())
    payload = {"iss": issuer, "aud": _AUDIENCE, "iat": now, "exp": now + 300, **claims}
    return {"Authorization": f"Bearer {jwt.encode(payload, _KEYS[issuer], algorithm='RS256')}"}


def _list(door: FrontDoor, headers: dict[str, str]):
    # The tenant argument only picks an API key, which no authenticator here reads.
    return door.post(TENANT_A, "tools/list", {}, extra_headers=headers)


_POLICIES = {TENANT_A: ("read_item",), TENANT_B: ("write_item",)}


def test_a_bound_issuer_is_refused_another_issuers_tenant() -> None:
    auth = _auth(a=[TENANT_A], b=[TENANT_B])
    with front_door(("read_item", "write_item"), _POLICIES, auth=auth) as door:
        response = _list(door, _bearer(_ISSUER_B, sub="alice", tenant_id=TENANT_A))

        assert response.status_code == 401


def test_a_bound_issuer_is_served_its_own_tenant() -> None:
    auth = _auth(a=[TENANT_A], b=[TENANT_B])
    with front_door(("read_item", "write_item"), _POLICIES, auth=auth) as door:
        own_b = _list(door, _bearer(_ISSUER_B, sub="alice", tenant_id=TENANT_B))
        own_a = _list(door, _bearer(_ISSUER_A, sub="alice", tenant_id=TENANT_A))

        assert own_b.status_code == 200
        assert [tool["name"] for tool in jsonrpc(own_b)["result"]["tools"]] == ["write_item"]
        assert [tool["name"] for tool in jsonrpc(own_a)["result"]["tools"]] == ["read_item"]


def test_without_a_binding_the_other_issuers_tenant_is_still_served() -> None:
    # Unchanged behaviour without the opt-in, which is what the boot warning is for.
    with front_door(("read_item", "write_item"), _POLICIES, auth=_auth()) as door:
        response = _list(door, _bearer(_ISSUER_B, sub="alice", tenant_id=TENANT_A))

        assert response.status_code == 200
        assert [tool["name"] for tool in jsonrpc(response)["result"]["tools"]] == ["read_item"]
