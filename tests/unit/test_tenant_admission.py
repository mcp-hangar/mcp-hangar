from types import SimpleNamespace
import threading
from unittest.mock import patch

import pytest

from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.batch.models import CallSpec
from mcp_hangar.server.tools.batch.tenant_admission import (
    configure_tenant_admission,
    TenantAdmission,
    TenantQuotaExceeded,
)


def limits(**overrides):
    return {"max_concurrency": 1, "rps": 1, "burst": 4, **overrides}


def test_busy_tenant_does_not_block_another_or_queue():
    admission = TenantAdmission({"a": limits(), "b": limits()})
    with admission.acquire("a"):
        with pytest.raises(TenantQuotaExceeded):
            with admission.acquire("a"):
                pytest.fail("same tenant exceeded concurrent budget")
        with admission.acquire("b"):
            pass
    with admission.acquire("a"):
        pass


def test_rate_budget_recovers_and_exception_releases_slot():
    with patch("mcp_hangar.server.tools.batch.tenant_admission.time.monotonic", return_value=10) as clock:
        admission = TenantAdmission({"a": limits(burst=1)})
        with pytest.raises(RuntimeError):
            with admission.acquire("a"):
                raise RuntimeError("upstream failure")
        with pytest.raises(TenantQuotaExceeded):
            with admission.acquire("a"):
                pass
        clock.return_value = 11
        with admission.acquire("a"):
            pass


@pytest.mark.parametrize("tenant", [None, "unknown"])
def test_unknown_or_missing_tenant_is_denied(tenant):
    with pytest.raises(TenantQuotaExceeded):
        with TenantAdmission({"a": limits()}).acquire(tenant):
            pass


@pytest.mark.parametrize(
    "budget", [limits(rps=float("nan")), limits(rps=True), limits(max_concurrency=0), limits(burst=-1)]
)
def test_invalid_limits_fail_at_configuration(budget):
    with pytest.raises(ValueError):
        TenantAdmission({"a": budget})


def test_serving_executor_refuses_before_entering_gates():
    configure_tenant_admission({"a": limits()})
    executor = BatchExecutor()
    identity = SimpleNamespace(caller=SimpleNamespace(tenant_id="b"))
    try:
        with (
            patch("mcp_hangar.server.tools.batch.executor.get_identity_context", return_value=identity),
            patch("mcp_hangar.server.tools.batch.executor.get_context"),
            patch.object(executor, "_execute_call_inner") as invoke,
        ):
            result = executor._execute_call(CallSpec(0, "test", "notes", "echo", {}), threading.Event(), 30, 0)
        assert not result.success
        assert result.error_type == "TenantQuotaExceeded"
        invoke.assert_not_called()
    finally:
        configure_tenant_admission({})
