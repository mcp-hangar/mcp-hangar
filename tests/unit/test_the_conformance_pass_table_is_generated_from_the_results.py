"""The pass table in the job summary comes from the run, not from a person."""

import contextlib
import importlib.util
import io
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

    assert "| `tools-list` | 2 | 0 | 0 | pass | |" in table
    assert "| `prompts-list` | 0 | 1 | 0 | known | |" in table
    assert "| `caching` | 1 | 1 | 0 | REGRESSION | |" in table
    assert "1 of 3 scenarios pass" in table


def test_a_skipped_check_is_neither_a_pass_nor_a_failure(tmp_path):
    # As in the suite: only FAILURE and WARNING count against a scenario. A
    # SKIPPED check once turned two passing client scenarios into "REGRESSION".
    _write(tmp_path, "http-standard-headers", ["SUCCESS", "SUCCESS", "SUCCESS", "SKIPPED", "SKIPPED"])
    _write(tmp_path, "tools-list", ["SKIPPED"])
    baseline = tmp_path / "baseline.yml"
    baseline.write_text("client:\n  - tools-list # #1\n")

    table = _load().render(tmp_path, baseline, "Client")

    assert "| `http-standard-headers` | 3 | 0 | 2 | pass | |" in table
    # Listed, and nothing failed: the suite calls that a stale entry.
    assert "| `tools-list` | 0 | 0 | 1 | stale | #1 |" in table


def test_a_scenario_the_revision_does_not_score_is_marked_and_left_out_of_the_count(tmp_path):
    # The suite runs `not_scored` scenarios but neither their failure nor a stale
    # baseline line moves its exit code, so the table must not call them stale or a regression.
    _write(tmp_path, "tools-list", ["SUCCESS"])
    _write(tmp_path, "tasks-status-notifications", ["SKIPPED"])
    _write(tmp_path, "tasks-lifecycle", ["FAILURE"])
    _write(tmp_path, "json-schema-2020-12", ["FAILURE"])
    baseline = tmp_path / "baseline.yml"
    baseline.write_text("server:\n  - tasks-status-notifications # #2307\n")
    requirements = tmp_path / "2026-07-28.yaml"
    requirements.write_text(
        "server:\n  - tools-list\n"
        "not_scored:\n"
        "  - scenario: tasks-status-notifications\n    leg: server\n    reason: extension\n"
        "  - scenario: tasks-lifecycle\n    leg: server\n    reason: extension\n"
        "  - scenario: json-schema-2020-12\n    leg: client\n    reason: pending\n"
    )

    table = _load().render(tmp_path, baseline, "Server", requirements=requirements)

    assert "| `tasks-status-notifications` | 0 | 0 | 1 | pass, not scored: extension | #2307 |" in table
    assert "| `tasks-lifecycle` | 0 | 1 | 0 | fail, not scored: extension | |" in table
    # Not scored for the client leg only: on the server leg it is an ordinary regression.
    assert "| `json-schema-2020-12` | 0 | 1 | 0 | REGRESSION | |" in table
    assert "1 of 2 scored scenarios pass;" in table
    assert "2 more ran but are not scored for this revision." in table


def test_the_headline_does_not_call_a_regression_known(tmp_path):
    # It used to count every non-pass as "known", which a published table would
    # then have stated about a regression.
    _write(tmp_path, "tools-list", ["SUCCESS"])
    _write(tmp_path, "prompts-list", ["FAILURE"])
    _write(tmp_path, "caching", ["WARNING"])
    baseline = tmp_path / "baseline.yml"
    baseline.write_text("server:\n  - prompts-list\n")

    table = _load().render(tmp_path, baseline, "Server")

    assert "1 of 3 scenarios pass; 1 are known failures in `baseline.yml`; 1 are regressions." in table


def test_a_listed_scenario_that_passes_is_reported_stale(tmp_path):
    # The suite fails such a run; the table says why rather than calling it a plain pass.
    _write(tmp_path, "tools-list", ["SUCCESS"])
    baseline = tmp_path / "baseline.yml"
    baseline.write_text("server:\n  - tools-list # #1234\n")

    table = _load().render(tmp_path, baseline, "Server")

    assert "| `tools-list` | 1 | 0 | 0 | stale | #1234 |" in table
    assert "1 pass but are still listed there (stale)" in table


def test_each_known_non_pass_names_the_issues_and_seps_on_its_baseline_line(tmp_path):
    _write(tmp_path, "server-sse-polling", ["FAILURE"])
    _write(tmp_path, "logging-set-level", ["FAILURE"])
    _write(tmp_path, "resources-list", ["FAILURE"])
    baseline = tmp_path / "baseline.yml"
    baseline.write_text(
        "server:\n"
        "  - server-sse-polling # needs test_reconnection (#2307); WARNINGs by decision, #2232\n"
        "  - logging-set-level # removed in 2026-07-28 (SEP-2575)\n"
        "  # -- 2. a section comment that mentions #888 --\n"
        "  - resources-list\n"
    )

    table = _load().render(tmp_path, baseline, "Server")

    assert "| `server-sse-polling` | 0 | 1 | 0 | known | #2307, #2232 |" in table
    assert "| `logging-set-level` | 0 | 1 | 0 | known | SEP-2575 |" in table
    # The next line's comment is not this entry's comment.
    assert "| `resources-list` | 0 | 1 | 0 | known | |" in table


def test_an_empty_cell_is_one_space_wide(tmp_path):
    # The docs lint tables in the compact style (MD060), where `|  |` is an error.
    _write(tmp_path, "tools-list", ["SUCCESS"])
    baseline = tmp_path / "baseline.yml"
    baseline.write_text("server:\n")

    table = _load().render(tmp_path, baseline, "Server")

    assert "|  |" not in table
    assert "| `tools-list` | 1 | 0 | 0 | pass | |" in table


def test_the_table_says_what_it_was_run_against(tmp_path):
    _write(tmp_path, "tools-list", ["SUCCESS"])
    baseline = tmp_path / "baseline.yml"
    baseline.write_text("server:\n")

    table = _load().render(tmp_path, baseline, "Server", suite="0.2.0-alpha.12", commit="f3d946c2" + "0" * 32)

    assert "Run with suite `@modelcontextprotocol/conformance@0.2.0-alpha.12`, Hangar at `f3d946c20000`." in table


def test_legs_written_as_json_assemble_into_one_page(tmp_path):
    module = _load()
    server, client = tmp_path / "server", tmp_path / "client"
    _write(server, "tools-list", ["SUCCESS"])
    folder = client / "tools_call-2026-10-22T21-37-53-848Z"
    folder.mkdir(parents=True)
    (folder / "checks.json").write_text(json.dumps([{"id": "c0", "status": "FAILURE"}]))
    baseline = tmp_path / "baseline-client.yml"
    baseline.write_text("client:\n  - tools_call # #2010\n")
    empty = tmp_path / "baseline.yml"
    empty.write_text("server:\n")

    legs = []
    for results, base, title in ((server, empty, "Server, 2026-07-28"), (client, baseline, "Client, 2026-07-28")):
        out = tmp_path / f"{results.name}.json"
        with contextlib.redirect_stdout(io.StringIO()):
            argv = ["x", str(results), str(base), title, "--suite", "0.2.0-alpha.12", "--json", str(out)]
            assert module.main(argv) == 0
        legs.append(str(out))

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert module.main(["x", "--page", *legs]) == 0
    doc = buffer.getvalue()

    assert doc.index("### Server, 2026-07-28") < doc.index("### Client, 2026-07-28")
    assert "| `tools-list` | 1 | 0 | 0 | pass | |" in doc
    assert "| `tools_call` | 0 | 1 | 0 | known | #2010 |" in doc


def test_client_results_are_read_with_their_auth_scenarios(tmp_path):
    # Client mode writes `<scenario>-<time>/`, and an `auth/` scenario one level down (#1902).
    for scenario, statuses in {"tools_call": ["SUCCESS"], "auth/pre-registration": ["SUCCESS", "FAILURE"]}.items():
        folder = tmp_path / f"{scenario}-2026-10-22T21-37-53-848Z"
        folder.mkdir(parents=True)
        (folder / "checks.json").write_text(json.dumps([{"id": f"c{i}", "status": s} for i, s in enumerate(statuses)]))
    baseline = tmp_path / "baseline-client.yml"
    baseline.write_text("client:\n  - auth/pre-registration # #2012\n")

    table = _load().render(tmp_path, baseline, "Client")

    assert "| `tools_call` | 1 | 0 | 0 | pass | |" in table
    assert "| `auth/pre-registration` | 1 | 1 | 0 | known | #2012 |" in table
    assert "1 of 2 scenarios pass" in table
