"""`RetryResult.attempt_count` equals the attempts the operation really made (#1677).

`attempts` lists the failed attempts that were followed by a retry; the last one
is not in it. The count added it only on success, so three real attempts that all
failed reported 2, and a first failure that was not retried reported 0 -- and
`retry_metadata.attempts` on a batch call said so.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_hangar.retry import BackoffStrategy, RetryPolicy, retry_async, retry_sync

POLICY = RetryPolicy(
    max_attempts=3,
    initial_delay=0.0,
    max_delay=0.0,
    backoff=BackoffStrategy.CONSTANT,
    jitter=False,
    retry_on=["timeout"],
)


class _Op:
    """Fails with *error* for the first *failures* calls, then returns "ok"."""

    def __init__(self, failures: int, error: Exception) -> None:
        self.failures, self.error, self.calls = failures, error, 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def _run(kind: str, operation):
    if kind == "sync":
        return retry_sync(operation, POLICY)
    return asyncio.run(retry_async(operation, POLICY))


@pytest.mark.parametrize("kind", ["sync", "async"])
class TestAttemptCountEqualsRealAttempts:
    def test_three_failures_are_three_attempts(self, kind):
        op = _Op(failures=99, error=TimeoutError("timeout"))
        result = _run(kind, op)

        assert result.success is False
        assert op.calls == 3
        assert result.attempt_count == 3

    def test_a_first_failure_that_is_not_retried_is_one_attempt(self, kind):
        op = _Op(failures=99, error=ValueError("bad argument"))
        result = _run(kind, op)

        assert result.success is False
        assert op.calls == 1
        assert result.attempt_count == 1

    def test_success_on_the_third_try_is_three_attempts(self, kind):
        op = _Op(failures=2, error=TimeoutError("timeout"))
        result = _run(kind, op)

        assert result.success is True
        assert op.calls == 3
        assert result.attempt_count == 3

    def test_success_on_the_first_try_is_one_attempt(self, kind):
        op = _Op(failures=0, error=TimeoutError("timeout"))
        result = _run(kind, op)

        assert result.attempt_count == op.calls == 1


def test_to_dict_reports_the_same_count():
    op = _Op(failures=99, error=TimeoutError("timeout"))

    assert retry_sync(op, POLICY).to_dict()["attempt_count"] == 3
