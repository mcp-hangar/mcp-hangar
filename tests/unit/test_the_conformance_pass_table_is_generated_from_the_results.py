"""The pass table in the job summary comes from the run, not from a person."""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "conformance_pass_table.py"


def _load():
    spec = importlib.util.spec_from_file_location("conformance_pass_table", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(results: Path, scenario: str, statuses: list[str]) -> None:
    folder = results / f"server-{scenario}-2026-10-18T11-36-34-993Z"
    folder.mkdir(parents=True)
    (folder / "checks.json").write_text(json.dumps([{"id": f"c{i}", "status": s} for i, s in enumerate(statuses)]))


def test_each_scenario_is_a_pass_a_known_failure_or_a_regression(tmp_path):
    _write(tmp_path, "tools-list", ["SUCCESS", "SUCCESS"])
    _write(tmp_path, "prompts-list", ["FAILURE"])
    _write(tmp_path, "caching", ["SUCCESS", "WARNING"])
    baseline = tmp_path / "baseline.yml"
    baseline.write_text("server:\n  - prompts-list # not advertised\n")

    table = _load().render(tmp_path, baseline, "Server")

    assert "| `tools-list` | 2 | 0 | pass |" in table
    assert "| `prompts-list` | 0 | 1 | known |" in table
    assert "| `caching` | 1 | 1 | REGRESSION |" in table
    assert "1 of 3 scenarios pass" in table
