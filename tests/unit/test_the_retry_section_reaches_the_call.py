"""The `retry:` config section decides how a batch call retries.

The section had a loader, a log line confirming what it loaded, and no
consumer. `RetryConfigStore.load_from_config` parsed `default_policy` and
`per_mcp_server`, merged them correctly and logged the result; the executor
then built `RetryPolicy(max_attempts=call.max_retries)` from the `hangar_batch`
argument alone. So `backoff`, `initial_delay`, `max_delay`, `retry_on` and
`jitter*` were always the class defaults, and `per_mcp_server` applied to
nothing at all (#1162).

The other half of this file is the rule that has to hold once the section is
live: a refusal is not a transient failure. `should_retry` matches `retry_on`
as a substring of both the type name and the message, so a broadened list --
or, for a denial whose message says "timeout", the stock list -- would have the
executor re-ask an approval gate once per attempt.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.domain.exceptions import (
    EgressPolicyApprovalRequiredError,
    EgressPolicyDeniedError,
    ToolAccessDeniedError,
    ValidationError,
)
from mcp_hangar.retry import (
    BackoffStrategy,
    RetryPolicy,
    get_retry_store,
    reset_retry_store,
    should_retry,
)
from mcp_hangar.server.tools.batch.executor import _retry_policy_for

_SERVER = "flaky"


@pytest.fixture(autouse=True)
def _clean_store():
    reset_retry_store()
    yield
    reset_retry_store()


def _call(mcp_server: str = _SERVER, max_retries: int = 1):
    return Mock(mcp_server=mcp_server, tool="t", max_retries=max_retries)


def _configure(section: dict) -> None:
    get_retry_store().load_from_config({"retry": section})


class TestWhatThePolicyIsMadeOf:
    def test_nothing_configured_and_no_argument_means_one_attempt(self):
        """The default this release ships with, unchanged."""
        assert _retry_policy_for(_call()) is None

    def test_nothing_configured_still_honours_the_caller(self):
        policy = _retry_policy_for(_call(max_retries=3))

        assert policy is not None
        assert policy.max_attempts == 3

    def test_a_per_server_policy_applies_without_any_argument(self):
        """The reported bug: this call used to be a single attempt."""
        _configure({"per_mcp_server": {_SERVER: {"max_attempts": 7, "backoff": "constant"}}})

        policy = _retry_policy_for(_call())

        assert policy is not None
        assert policy.max_attempts == 7
        assert policy.backoff is BackoffStrategy.CONSTANT

    def test_the_whole_policy_comes_from_config_not_just_the_count(self):
        _configure(
            {
                "default_policy": {
                    "max_attempts": 4,
                    "backoff": "linear",
                    "initial_delay": 0.25,
                    "max_delay": 2.0,
                    "jitter": False,
                }
            }
        )

        policy = _retry_policy_for(_call())

        assert policy is not None
        assert (policy.backoff, policy.initial_delay, policy.max_delay, policy.jitter) == (
            BackoffStrategy.LINEAR,
            0.25,
            2.0,
            False,
        )

    def test_a_default_policy_reaches_a_server_with_no_entry_of_its_own(self):
        _configure({"default_policy": {"max_attempts": 2}, "per_mcp_server": {"other": {"max_attempts": 9}}})

        policy = _retry_policy_for(_call())

        assert policy is not None
        assert policy.max_attempts == 2


class TestConfigIsTheCeiling:
    def test_the_caller_may_lower_the_attempt_count(self):
        _configure({"per_mcp_server": {_SERVER: {"max_attempts": 5}}})

        policy = _retry_policy_for(_call(max_retries=2))

        assert policy is not None
        assert policy.max_attempts == 2

    def test_the_caller_may_not_raise_it(self):
        """The operator owns how hard this gateway leans on an upstream."""
        _configure({"per_mcp_server": {_SERVER: {"max_attempts": 2}}})

        policy = _retry_policy_for(_call(max_retries=50))

        assert policy is not None
        assert policy.max_attempts == 2

    def test_a_configured_single_attempt_means_no_retry_block_at_all(self):
        _configure({"per_mcp_server": {_SERVER: {"max_attempts": 1}}})

        assert _retry_policy_for(_call(max_retries=9)) is None


class TestARefusalIsNeverRetried:
    @pytest.mark.parametrize(
        "error",
        [
            ToolAccessDeniedError("t", _SERVER),
            EgressPolicyDeniedError("t", _SERVER, "denied by policy"),
            EgressPolicyApprovalRequiredError("t", _SERVER, "approval required"),
            ValidationError("malformed arguments"),
        ],
    )
    def test_not_even_when_retry_on_names_everything(self, error):
        # `retry_on` is a substring match on the type name, so "Error" matches
        # every refusal there is.
        policy = RetryPolicy(max_attempts=3, retry_on=["Error", "Exception"])

        assert should_retry(error, policy) is False

    def test_not_even_when_the_message_reads_like_a_timeout(self):
        """The stock `retry_on` list matches the MESSAGE too."""
        error = ToolAccessDeniedError("t", _SERVER)
        error.args = ("connection to the approver timed out",)

        assert should_retry(error, RetryPolicy()) is False

    def test_a_transient_failure_still_retries(self):
        assert should_retry(TimeoutError("upstream timed out"), RetryPolicy()) is True


class TestThroughTheExecutor:
    def test_a_configured_policy_drives_the_attempts_of_a_real_call(self):
        """End to end: config -> hangar_batch (no max_attempts) -> attempts."""
        from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec

        _configure({"per_mcp_server": {_SERVER: {"max_attempts": 3, "initial_delay": 0.0, "jitter": False}}})

        attempts: list[int] = []

        ctx = Mock()
        ctx.event_bus = Mock()
        ctx.get_mcp_server.return_value = Mock(
            state=Mock(value="ready"), has_tools=False, health=Mock(should_degrade=Mock(return_value=False))
        )
        ctx.mcp_server_exists.return_value = True

        def _send(_command):
            attempts.append(1)
            raise TimeoutError("upstream timed out")

        ctx.command_bus = Mock(send=Mock(side_effect=_send))

        with (
            patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
            patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
            patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
        ):
            exec_groups.get.return_value = None
            val_groups.get.return_value = None
            result = BatchExecutor().execute(
                batch_id="b",
                calls=[CallSpec(index=0, call_id="c", mcp_server=_SERVER, tool="t", arguments={})],
                max_concurrency=1,
                global_timeout=30.0,
                fail_fast=False,
            )

        assert result.results[0].success is False
        assert len(attempts) == 3, f"config asked for 3 attempts, made {len(attempts)}"
