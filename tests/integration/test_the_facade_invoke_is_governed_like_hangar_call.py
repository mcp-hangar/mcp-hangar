"""`Hangar.invoke` is governed as `hangar_call` is, on one real boot (#1453).

The facade's ``invoke`` called the server directly, so none of the call-time
controls the configuration set applied to it. It now runs the executor
``hangar_call`` runs. ``_facade_invoke_harness.py`` boots ``Hangar.from_config``
in a fresh interpreter per mode, over a real HTTP upstream. Nothing is stubbed.

``controls``: each call is made twice, by the same caller, through
``Hangar.invoke`` and as ``hangar_call`` through the app ``serve --http``
serves, authenticated by the caller's API key.

* A tool the tool-access policy denies, a withdrawn tool and a call a validator
  rejects are refused, with the code and the text ``hangar_call`` gives.
* A tenant over its budget is refused, and so is an anonymous caller, which
  carries no tenant, when the budgets give a caller with no tenant none.
* An allowed call returns what ``hangar_call`` returns as its result.
* Only the calls that were let through reach the upstream.

``truncation``: truncation is not one of those controls. With a truncation
budget configured, ``invoke`` still returns the whole result and stores no
continuation, while the same call through ``hangar_call`` is cut.

``auth``: with authentication on, a principal holding ``tool:invoke`` is
allowed, one without it and an anonymous caller are refused, and the system
principal, which authorization would grant everything, is refused before
anything runs.

The unit-level mapping of every outcome is in ``tests/unit/test_facade.py``,
and the caller's authorization in ``tests/unit/test_tool_invoke_authz.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_facade_invoke_harness.py")
MODES = ("controls", "truncation", "auth")

TOO_FAST = "This tenant's execution budget is exhausted: calls started too fast"
NO_BUDGET = "No execution budget is configured for this tenant"

#: Each refused case, and the code `hangar_call` refuses it with.
REFUSED = {
    "denied": "ToolAccessDeniedError",
    "withdrawn": "ToolWithdrawnError",
    "validator": "ValidatorDenied",
    "over_budget": "TenantQuotaExceeded",
    "anonymous": "TenantQuotaExceeded",
}


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return dict(json.loads(out.read_text()))


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("facade-invoke")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


@pytest.fixture
def run(runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return runs["controls"]


@pytest.mark.parametrize(("case", "code"), sorted(REFUSED.items()))
def test_a_refused_call_is_refused_as_hangar_call_refuses_it(run: dict[str, Any], case: str, code: str) -> None:
    facade = run["cases"][case]["facade"]
    served = run["cases"][case]["hangar_call"]

    assert served["success"] is False and served["error_type"] == code, served
    assert facade == {"ok": False, "exception": "ToolCallFailedError", "code": code, "message": served["error"]}


def test_the_tenant_over_its_budget_had_spent_it(run: dict[str, Any]) -> None:
    assert run["tenant_b_first"]["ok"] is True, run["tenant_b_first"]
    assert run["cases"]["over_budget"]["hangar_call"]["error"] == TOO_FAST


def test_an_anonymous_caller_is_refused_the_budget_of_a_caller_with_no_tenant(run: dict[str, Any]) -> None:
    assert run["cases"]["anonymous"]["hangar_call"]["error"] == NO_BUDGET


def test_an_allowed_call_returns_the_result_hangar_call_returns(run: dict[str, Any]) -> None:
    facade = run["cases"]["allowed"]["facade"]
    served = run["cases"]["allowed"]["hangar_call"]

    assert served["success"] is True, served
    assert facade == {"ok": True, "result": served["result"]}
    assert "did read_item" in json.dumps(facade["result"])


def test_only_the_calls_let_through_reach_the_upstream(run: dict[str, Any]) -> None:
    # tenant:b's first call, and the allowed call on each surface.
    assert run["upstream_called"] == ["read_item"] * 3


def test_with_truncation_configured_invoke_returns_the_whole_result(runs: dict[str, dict[str, Any]]) -> None:
    truncation = runs["truncation"]
    whole = runs["controls"]["cases"]["allowed"]["facade"]["result"]

    assert truncation["invoked"] == {"ok": True, "result": whole}, truncation["invoked"]
    assert truncation["cached_after_invoke"] == 0


def test_the_same_call_through_hangar_call_is_cut(runs: dict[str, dict[str, Any]]) -> None:
    # The control: truncation is on, and the budget cuts this result.
    truncation = runs["truncation"]
    served = truncation["hangar_call"]

    assert served["success"] is True and served.get("truncated") is True, served
    assert served.get("continuation_id"), served
    assert truncation["cached_after_hangar_call"] == 1


def test_with_auth_on_a_principal_holding_tool_invoke_is_allowed(runs: dict[str, dict[str, Any]]) -> None:
    auth = runs["auth"]

    assert auth["auth_enabled"] is True
    assert auth["with_role"]["ok"] is True, auth["with_role"]
    assert "did read_item" in json.dumps(auth["with_role"]["result"])


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("without_role", "Not authorized to invoke tool 'read_item': tool:invoke permission required"),
        ("anonymous", "Authentication required to invoke tools"),
    ],
)
def test_with_auth_on_a_caller_without_tool_invoke_is_refused(
    runs: dict[str, dict[str, Any]], case: str, message: str
) -> None:
    refused = runs["auth"][case]

    assert refused == {
        "ok": False,
        "exception": "ToolCallFailedError",
        "code": "AuthorizationDenied",
        "message": message,
    }


def test_the_system_principal_is_refused_before_anything_runs(runs: dict[str, dict[str, Any]]) -> None:
    auth = runs["auth"]

    assert auth["system"]["ok"] is False and auth["system"]["exception"] == "ValueError", auth["system"]
    assert "system principal" in auth["system"]["message"]
    # Only the principal holding the role reached the upstream.
    assert auth["upstream_called"] == ["read_item"]
