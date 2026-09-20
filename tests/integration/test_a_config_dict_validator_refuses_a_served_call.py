"""A validator passed in a config dict refuses a real call through the served app (#1415).

``bootstrap(config_dict=...)`` never registered ``interceptors.validators``: only
the file loader did. So an embedder or a harness that passed a ``payload_size``
cap in a dict ran with no cap at all, and nothing said so.

Each mode runs ``_config_dict_served_harness.py`` in a fresh interpreter: the
real ``bootstrap()`` -- from a file, or from the same dict -- then ``hangar_call``
through the app ``serve --http`` serves. No stub, no mock context. The unit-level
parity over every section is ``tests/unit/test_a_config_dict_boots_like_a_file.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).with_name("_config_dict_served_harness.py")
MODES = ("file", "dict")


def _run(mode: str, tmp: Path) -> dict[str, Any]:
    out = tmp / mode / "run.json"
    out.parent.mkdir()
    result = subprocess.run(
        [sys.executable, str(HARNESS), mode, str(out)],
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 0 and out.exists(), (
        f"{mode}: harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    )
    return json.loads(out.read_text())["batches"]


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("config-dict")
    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        pending = {mode: pool.submit(_run, mode, tmp) for mode in MODES}
        return {mode: future.result() for mode, future in pending.items()}


def _only_result(batch: dict[str, Any]) -> dict[str, Any]:
    (result,) = batch["results"]
    return result


def test_the_dict_s_validator_refuses_an_oversized_call(runs) -> None:
    refused = _only_result(runs["dict"]["oversized"])

    assert refused["success"] is False
    assert refused["error_type"] == "ValidatorDenied"
    assert "exceeds cap 256" in refused["error"]


def test_the_same_boot_serves_a_call_under_the_cap(runs) -> None:
    # So the refusal above is the validator's, not a boot that serves nothing.
    assert _only_result(runs["dict"]["small"])["success"] is True


def test_a_file_and_a_dict_answer_both_calls_alike(runs) -> None:
    def outcome(batch: dict[str, Any]) -> tuple[Any, ...]:
        result = _only_result(batch)
        return result["success"], result.get("error_type"), result.get("error")

    assert {name: outcome(batch) for name, batch in runs["dict"].items()} == {
        name: outcome(batch) for name, batch in runs["file"].items()
    }
