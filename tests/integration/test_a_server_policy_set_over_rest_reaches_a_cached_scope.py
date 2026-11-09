"""A server-scope policy set over REST applies to a caller whose policy is already cached (#2203).

The resolver caches each caller's effective policy. Setting a provider policy
dropped only the cache entries that named that server, so a policy every scope
reads -- ``_global``, or one set on a group id, which the group branch is asked
with -- did not reach a caller who had called before: the call that should have
been refused was served until the entry went for another reason.

``_policy_cache_harness.py`` runs in a fresh interpreter: the real
``bootstrap()`` from a file, real stdio subprocess servers, REST and MCP calls
under an API key that carries a tenant.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.timeout(120)

HARNESS = Path(__file__).with_name("_policy_cache_harness.py")
DENIED = "ToolAccessDeniedError"
SERVED = {"plain": "served", "member": "served", "pool": "served"}


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    workdir = tmp_path_factory.mktemp("policy-cache")
    out = workdir / "run.json"
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(workdir), str(out)],
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-6000:]}"
    return dict(json.loads(out.read_text()))


def test_every_call_is_served_before_the_change(run: dict[str, Any]) -> None:
    assert run["add_before"] == SERVED
    assert run["echo_before"] == SERVED


def test_a_provider_policy_on_a_group_id_reaches_the_cached_group_scopes(run: dict[str, Any]) -> None:
    # `member` is asked under its own scope and under `pool`'s, so the group's
    # deny reaches it named directly too. `plain` never read it.
    assert run["add_after_group_id_policy"] == {"plain": "served", "member": DENIED, "pool": DENIED}


def test_a_global_policy_reaches_every_cached_scope(run: dict[str, Any]) -> None:
    assert run["echo_after_global_policy"] == {"plain": DENIED, "member": DENIED, "pool": DENIED}
