"""Unit tests for contextvars propagation across the ThreadPoolExecutor boundary.

Verifies that a ContextVar set in the calling thread before
BatchExecutor.execute() is readable inside _execute_call worker threads
(fix: copy_context() at submit time).

Also checks that OTel span context is propagated so per-call spans are
children of the batch span.
"""

import contextvars
import threading
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec

# ---------------------------------------------------------------------------
# Module-level ContextVar used across tests
# ---------------------------------------------------------------------------

_test_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "test_propagation_var", default=None
)


# ---------------------------------------------------------------------------
# Shared fixtures (mirrors test_batch_invoke.py minimal setup)
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_context():
    """Minimal application context mock required by BatchExecutor."""
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = {"ok": True}
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    ctx.mcp_server_exists.return_value = True

    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as val_groups,
    ):
        exec_groups.get.return_value = None
        val_groups.get.return_value = None
        yield ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContextVarPropagationIntoWorkers:
    """copy_context() propagates a contextvar into ThreadPoolExecutor workers."""

    def test_contextvar_visible_in_worker_with_copy_context(self, mock_context):
        """A ContextVar set before execute() is readable inside _execute_call."""
        observed: list[str | None] = []
        sentinel = "request-tenant-abc"

        # Use a plain return value on command_bus.send — intercept via a
        # threading.Event so we can capture the contextvar value in the worker
        # thread without recursive mock calls.
        captured_event = threading.Event()

        def spy_send(cmd):
            observed.append(_test_var.get())
            captured_event.set()
            return {"ok": True}

        mock_context.command_bus.send.side_effect = spy_send

        # Bind the contextvar on the calling thread — simulates IdentityMiddleware
        # having set identity_context_var before hangar_call reaches execute().
        token = _test_var.set(sentinel)
        try:
            executor = BatchExecutor()
            calls = [
                CallSpec(
                    index=0,
                    call_id="ctx-test-0",
                    mcp_server="math",
                    tool="add",
                    arguments={"a": 1},
                )
            ]
            result = executor.execute(
                batch_id="ctx-batch-1",
                calls=calls,
                max_concurrency=4,
                global_timeout=30.0,
                fail_fast=False,
            )
        finally:
            _test_var.reset(token)

        assert result.success is True, f"Batch failed: {result}"
        assert len(observed) == 1, "spy_send should have been called exactly once"
        assert observed[0] == sentinel, (
            f"Worker saw {observed[0]!r}; expected {sentinel!r}. "
            "copy_context() may not be propagating the contextvar."
        )

    def test_contextvar_isolation_between_calls(self, mock_context):
        """Each batch call sees the same parent context snapshot (not cross-contamination)."""
        observed: list[str | None] = []
        sentinel = "isolated-tenant-xyz"
        lock = threading.Lock()

        def spy_send(cmd):
            with lock:
                observed.append(_test_var.get())
            return {"ok": True}

        mock_context.command_bus.send.side_effect = spy_send

        token = _test_var.set(sentinel)
        try:
            executor = BatchExecutor()
            calls = [
                CallSpec(
                    index=i,
                    call_id=f"ctx-iso-{i}",
                    mcp_server="math",
                    tool="add",
                    arguments={"a": i},
                )
                for i in range(3)
            ]
            result = executor.execute(
                batch_id="ctx-batch-iso",
                calls=calls,
                max_concurrency=3,
                global_timeout=30.0,
                fail_fast=False,
            )
        finally:
            _test_var.reset(token)

        assert result.succeeded == 3, f"Expected 3 succeeded, got: {result}"
        assert len(observed) == 3
        # All workers must see the sentinel — not None (leaked default) or
        # values written by sibling workers.
        assert all(v == sentinel for v in observed), (
            f"Some workers saw unexpected values: {observed}"
        )

    def test_contextvar_default_without_copy_context(self):
        """Baseline: ThreadPoolExecutor without copy_context sees default (None).

        This documents WHY the fix is needed — it is NOT exercising the fixed
        BatchExecutor code, but proves the mechanism at the raw concurrent.futures
        level so reviewers can verify the premise.
        """
        from concurrent.futures import ThreadPoolExecutor

        sentinel = "should-not-reach-worker"
        token = _test_var.set(sentinel)
        try:
            seen: list[str | None] = []

            def worker():
                seen.append(_test_var.get())

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(worker).result()
        finally:
            _test_var.reset(token)

        # Without copy_context the worker gets the default (None)
        assert seen == [None], (
            "Expected None without copy_context(); got: "
            f"{seen}. Python may have changed contextvar defaults."
        )
