"""``max_attempts=1`` means one attempt even when the gateway configures retries (#1678).

``_retry_policy_for`` read 1 as "unset", so with a ``retry:`` block the default
and an explicit 1 both got the configured count (3) and a caller could only lower
it to 2. A tool that is not idempotent has to be callable once.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp_hangar.retry import RetryPolicy
from mcp_hangar.server.tools.batch.executor import _retry_policy_for
from mcp_hangar.server.tools.batch.models import CallSpec

CONFIGURED = RetryPolicy(max_attempts=3)


def _call(max_retries: int | None = None) -> CallSpec:
    return CallSpec(index=0, call_id="c", mcp_server="srv", tool="t", arguments={}, max_retries=max_retries)


def _attempts(configured: RetryPolicy | None, asked: int | None) -> int:
    with patch("mcp_hangar.server.tools.batch.executor.configured_retry_policy", return_value=configured):
        policy = _retry_policy_for(_call(asked))
    return 1 if policy is None else policy.max_attempts


@pytest.mark.parametrize(
    ("asked", "expected"),
    [(None, 3), (1, 1), (2, 2), (3, 3), (10, 3)],
    ids=["unset-takes-the-configured-count", "one-is-one", "lowers", "equals", "never-raises"],
)
def test_with_a_configured_policy_the_caller_may_lower_it_to_one_and_never_raise_it(asked, expected):
    assert _attempts(CONFIGURED, asked) == expected


@pytest.mark.parametrize(("asked", "expected"), [(None, 1), (1, 1), (4, 4)])
def test_with_no_configured_policy_nothing_changes(asked, expected):
    assert _attempts(None, asked) == expected


def test_a_policy_configured_for_one_attempt_retries_nothing():
    assert _attempts(RetryPolicy(max_attempts=1), None) == 1
