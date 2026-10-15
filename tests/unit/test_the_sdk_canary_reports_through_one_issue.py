"""The SDK canary opens, updates and closes one issue from a JUnit file (#1793).

The failing run is real: pytest, run here on a throwaway test that fails inside
Hangar's own code, writes the JUnit file the workflow's run would write. `gh` is
a recorder, so the report/update path is exercised without a disposable canary
run and without putting a failure into main.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sdk_canary_report", ROOT / "scripts" / "sdk_canary_report.py")
assert _spec is not None and _spec.loader is not None
canary = importlib.util.module_from_spec(_spec)
sys.modules["sdk_canary_report"] = canary
_spec.loader.exec_module(canary)

FAILING = textwrap.dedent(
    """
    from mcp_hangar.domain.value_objects import MaxConsecutiveFailures

    def test_one():
        MaxConsecutiveFailures(0)

    def test_two():
        MaxConsecutiveFailures(500)

    def test_three():
        assert 1 == 2, "not from hangar"

    def test_fine():
        assert True
    """
)


def _junit(tmp_path: Path, body: str) -> Path:
    test_file = tmp_path / "test_canary_fixture.py"
    test_file.write_text(body)
    junit = tmp_path / "junit.xml"
    subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "--tb=short", f"--junitxml={junit}", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    return junit


class Gh:
    """Records `gh` calls and answers `issue list` with the issues it was given."""

    def __init__(self, open_issues: list[dict] | None = None) -> None:
        self.open_issues = open_issues or []
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.open_issues)
        if args[:2] == ["issue", "create"]:
            return "https://github.com/o/r/issues/99\n"
        return ""

    def verbs(self) -> list[str]:
        return [" ".join(c[:2]) for c in self.calls if c[:2] != ["issue", "list"]]


def test_failures_are_grouped_by_the_first_frame_in_hangars_code(tmp_path):
    failures, ran = canary.parse_junit(_junit(tmp_path, FAILING))

    assert ran == 4 and len(failures) == 3
    grouped = canary.group_by_frame(failures)
    first = next(iter(grouped))
    assert "mcp_hangar/" in first and len(grouped[first]) == 2, grouped.keys()
    assert [f.test.rsplit("::", 1)[-1] for f in grouped[first]] == ["test_one", "test_two"]
    other = [frame for frame in grouped if frame != first]
    assert len(other) == 1 and "not from hangar" in other[0], other


def test_a_failing_run_opens_one_issue_titled_with_the_version(tmp_path):
    gh = Gh()
    junit = _junit(tmp_path, FAILING)

    code = canary.main(
        ["--junit", str(junit), "--sdk-version", "2.4.0", "--repo", "o/r", "--run-url", "https://run/1"], gh=gh
    )

    assert code == 0 and gh.verbs() == ["issue create"]
    create = next(c for c in gh.calls if c[:2] == ["issue", "create"])
    assert create[create.index("--title") + 1] == "sdk canary: mcp 2.4.0"
    body = create[create.index("--body") + 1]
    assert "fails 3 of 4 tests" in body and "https://run/1" in body and "test_one" in body


def test_a_failing_run_updates_the_open_issue_and_follows_the_version(tmp_path):
    gh = Gh([{"number": 7, "title": "sdk canary: mcp 2.3.1"}])

    canary.main(["--junit", str(_junit(tmp_path, FAILING)), "--sdk-version", "2.4.0", "--repo", "o/r"], gh=gh)

    assert gh.verbs() == ["issue edit"]
    edit = next(c for c in gh.calls if c[:2] == ["issue", "edit"])
    assert edit[2] == "7" and edit[edit.index("--title") + 1] == "sdk canary: mcp 2.4.0"


def test_a_duplicate_is_closed_in_favour_of_the_first(tmp_path):
    gh = Gh([{"number": 7, "title": "sdk canary: mcp 2.3.1"}, {"number": 8, "title": "sdk canary: mcp 2.3.0"}])

    canary.main(["--junit", str(_junit(tmp_path, FAILING)), "--sdk-version", "2.4.0", "--repo", "o/r"], gh=gh)

    assert gh.verbs() == ["issue edit", "issue close"]
    assert gh.calls[-1][2] == "8" and gh.calls[-1][-1] == "Superseded by #7."


def test_a_green_run_closes_the_open_issue(tmp_path):
    gh = Gh([{"number": 7, "title": "sdk canary: mcp 2.4.0"}])
    junit = _junit(tmp_path, "def test_ok():\n    assert True\n")

    code = canary.main(["--junit", str(junit), "--sdk-version", "2.4.1", "--repo", "o/r", "--run-url", "u"], gh=gh)

    assert code == 0 and gh.verbs() == ["issue close"]
    assert "mcp 2.4.1, passes" in " ".join(gh.calls[-1])


def test_a_green_run_with_nothing_open_does_nothing(tmp_path):
    gh = Gh()
    canary.main(
        ["--junit", str(_junit(tmp_path, "def test_ok():\n    pass\n")), "--sdk-version", "2.4.1", "--repo", "o/r"],
        gh=gh,
    )

    assert gh.verbs() == []


def test_a_junit_file_with_no_tests_is_not_a_green_run(tmp_path):
    gh = Gh([{"number": 7, "title": "sdk canary: mcp 2.4.0"}])
    empty = tmp_path / "junit.xml"
    empty.write_text('<testsuites><testsuite name="pytest" tests="0" failures="0"/></testsuites>')

    code = canary.main(["--junit", str(empty), "--sdk-version", "2.4.1", "--repo", "o/r"], gh=gh)

    assert code == 1 and gh.calls == []


def test_an_issue_that_is_not_a_canary_is_never_touched(tmp_path):
    gh = Gh([{"number": 3, "title": "something else"}])

    canary.main(["--junit", str(_junit(tmp_path, FAILING)), "--sdk-version", "2.4.0", "--repo", "o/r"], gh=gh)

    assert gh.verbs() == ["issue create"]


@pytest.mark.parametrize("flag", ["--dry-run"])
def test_dry_run_prints_the_issue_and_calls_nothing(tmp_path, capsys, flag):
    gh = Gh()

    canary.main(["--junit", str(_junit(tmp_path, FAILING)), "--sdk-version", "2.4.0", "--repo", "o/r", flag], gh=gh)

    assert "sdk canary: mcp 2.4.0" in capsys.readouterr().out and gh.calls == []
