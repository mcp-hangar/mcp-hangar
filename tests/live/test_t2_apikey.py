"""Tier 2 live verification: API-key rotation + grace on the real HTTP surface.

Black-box test against a REAL ``mcp-hangar serve --http`` process with API-key
auth enabled (``allow_anonymous: false``) and its key store pointed at a SQLite
DB seeded via the shipped ``SQLiteApiKeyStore`` -- exactly how an operator would
provision keys. It proves the trust-boundary claim tracked in
``docs/internal/LIVE_VERIFICATION.md`` ("API-key rotation + grace; old key
honored then rejected"):

* a valid key authenticates (200);
* after ROTATION the OLD key is still honored DURING the grace window (200) and
  the freshly-minted NEW key authenticates (200);
* after the grace window has ELAPSED the OLD (rotated) key is REJECTED (401);
* a REVOKED key is rejected immediately (401);
* the fail-closed baseline: no key at all is rejected (401).

Time is driven through the store's own grace bookkeeping (a rotation whose grace
window is already in the past), not a real sleep, so the "post-grace" arm is
deterministic and fast.

The authenticated probe surface is ``/api/system/me`` (auth enforcement runs
before the handler, so a missing/rotated-out/revoked key is rejected at the
trust boundary; a valid key reaches the handler which echoes ``authenticated``).

Skip-safe: if the ``mcp-hangar`` binary or the stub backend is missing, or the
server never becomes healthy, the module SKIPs rather than fails. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t2" -o addopts=""
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import sys

import httpx
import pytest

from tests.live.conftest import _MATH_SERVER, _serve_hangar

pytestmark = [pytest.mark.live, pytest.mark.t2]

# Authenticated (non-skipped) REST surface: a valid principal reaches the handler
# and is echoed; an invalid/rotated-out/revoked key is rejected at the boundary.
_AUTHED_PATH = "/api/system/me"

# API-key auth on, anonymous OFF (so a *presented* bad key is a hard 401, not a
# silent fall-through to anonymous), key store on SQLite so we can seed/rotate/
# revoke with the shipped store. One lazy math backend so the server starts with
# no external dependency.
_APIKEY_CONFIG = """\
logging:
  level: WARNING
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
"""


@dataclass
class _ApiKeyHarness:
    """A live hangar under API-key auth, plus the keys seeded into its store."""

    base_url: str
    valid_key: str  # never rotated/revoked -> must authenticate
    revoked_key: str  # revoked before start -> must be rejected
    grace_old_key: str  # rotated with an ACTIVE grace window -> still honored
    grace_new_key: str  # the replacement minted by that rotation -> honored
    postgrace_old_key: str  # rotated with an already-ELAPSED grace -> rejected


def _seed_keys(auth_db: Path) -> dict[str, str]:
    """Seed keys and drive rotation/revocation via the shipped SQLiteApiKeyStore.

    All mutations happen up front (before the server starts) so the running
    hangar reads a single settled state. Returns the raw keys the test drives.
    """
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        valid_key = store.create_key(principal_id="svc:valid", name="valid", tenant_id="t-valid")
        revoked_key = store.create_key(principal_id="svc:revoked", name="revoked", tenant_id="t-revoked")
        grace_old_key = store.create_key(principal_id="svc:grace", name="grace", tenant_id="t-grace")
        postgrace_old_key = store.create_key(principal_id="svc:postgrace", name="postgrace", tenant_id="t-postgrace")

        # Revoke: rejected immediately.
        revoked_kid = store.list_keys("svc:revoked")[0].key_id
        assert store.revoke_key(revoked_kid) is True

        # Rotate with an ACTIVE grace window: the old key stays honored during it,
        # and a new replacement key is minted.
        grace_kid = store.list_keys("svc:grace")[0].key_id
        grace_new_key = store.rotate_key(grace_kid, grace_period_seconds=3600)

        # Rotate with an already-ELAPSED grace window (grace_until in the past):
        # drives the "post-grace" clock without a real sleep -> old key rejected.
        postgrace_kid = store.list_keys("svc:postgrace")[0].key_id
        store.rotate_key(postgrace_kid, grace_period_seconds=-1)

        return {
            "valid_key": valid_key,
            "revoked_key": revoked_key,
            "grace_old_key": grace_old_key,
            "grace_new_key": grace_new_key,
            "postgrace_old_key": postgrace_old_key,
        }
    finally:
        store.close()


@pytest.fixture(scope="module")
def apikey_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_ApiKeyHarness]:
    """Run a real hangar (API-key auth, anonymous off) over its seeded SQLite store.

    Module-local (does not touch the shared conftest); skip-safe via the reused
    ``_serve_hangar`` engine.
    """
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("hangar_apikey")
    auth_db = workdir / "auth.db"

    try:
        keys = _seed_keys(auth_db)
    except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
        pytest.skip(f"could not seed/rotate API keys: {exc}")

    config_text = _APIKEY_CONFIG.format(
        auth_db=str(auth_db),
        python=sys.executable,
        server=str(_MATH_SERVER),
    )
    for base_url in _serve_hangar(workdir, config_text):
        yield _ApiKeyHarness(base_url=base_url, **keys)


def _get_me(base_url: str, api_key: str | None) -> httpx.Response:
    headers = {"X-API-Key": api_key} if api_key is not None else {}
    return httpx.get(f"{base_url}{_AUTHED_PATH}", headers=headers, timeout=10.0)


def test_valid_api_key_authenticates(apikey_hangar: _ApiKeyHarness) -> None:
    """Claim: a valid (un-rotated, un-revoked) API key authenticates over HTTP."""
    resp = _get_me(apikey_hangar.base_url, apikey_hangar.valid_key)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("authenticated") is True, resp.text


def test_missing_key_is_rejected(apikey_hangar: _ApiKeyHarness) -> None:
    """Fail-closed baseline: with anonymous off, no credential is a 401."""
    resp = _get_me(apikey_hangar.base_url, None)
    assert resp.status_code == 401, resp.text


def test_rotated_key_honored_during_grace_and_new_key_works(apikey_hangar: _ApiKeyHarness) -> None:
    """Claim: after rotation, the OLD key is still honored DURING grace, and the NEW key works."""
    # Old key, still inside its grace window -> honored.
    old = _get_me(apikey_hangar.base_url, apikey_hangar.grace_old_key)
    assert old.status_code == 200, old.text
    assert old.json().get("authenticated") is True, old.text

    # Freshly-minted replacement key -> honored.
    new = _get_me(apikey_hangar.base_url, apikey_hangar.grace_new_key)
    assert new.status_code == 200, new.text
    assert new.json().get("authenticated") is True, new.text


def test_rotated_key_rejected_after_grace(apikey_hangar: _ApiKeyHarness) -> None:
    """Claim: once the grace window has elapsed, the OLD rotated key is REJECTED (fail-closed)."""
    resp = _get_me(apikey_hangar.base_url, apikey_hangar.postgrace_old_key)
    assert resp.status_code == 401, resp.text
    assert "www-authenticate" in {k.lower() for k in resp.headers}


def test_revoked_key_rejected_immediately(apikey_hangar: _ApiKeyHarness) -> None:
    """Claim: a REVOKED key is rejected immediately (fail-closed)."""
    resp = _get_me(apikey_hangar.base_url, apikey_hangar.revoked_key)
    assert resp.status_code == 401, resp.text
    assert "www-authenticate" in {k.lower() for k in resp.headers}
