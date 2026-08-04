"""The complexity baseline may shrink. It may not grow.

`C901` caps new code at cyclomatic complexity 15. Fourteen functions still
exceed that, and each carries an explicit `# noqa: C901 -- baseline CC=N`. Ruff
alone cannot tell a legitimate baseline entry from a new one someone added to
silence the gate, so this test does: the count is capped, and lowering the cap is
the unit of progress.

Why 15 and not mccabe's default 10: a threshold of 10 would have baselined 54
functions, and a 54-entry list of suppressions stops being read as debt. A list
this short stays a to-do list.

The cap came down from 16 as functions were split. `MetricsEventHandler.handle`
was the most recent: a 19-branch isinstance chain at CC=20, replaced by a
dispatch table when adding a twentieth branch became necessary. Its own noqa
said "split before extending", and the gate is what made that stick.

The named worst offenders below are pinned separately, because they are the ones
a reader should recognise: `init_command` at 49 and `_load_mcp_server_config` at
37 are the two largest, and `_execute_call_inner` at 36 is the hot path every
enforcement decision goes through.
"""

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "mcp_hangar"

_NOQA = re.compile(r"#\s*noqa:\s*C901\b[^\n]*?baseline CC=(\d+)")

# Lower these as functions are split. Never raise them.
MAX_BASELINED_FUNCTIONS = 14
MAX_BASELINED_COMPLEXITY = 49


def _baseline_entries() -> list[tuple[str, int, int]]:
    """Return (repo-relative path, line number, recorded complexity)."""
    found: list[tuple[str, int, int]] = []
    for path in SRC.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _NOQA.search(line)
            if match:
                found.append((str(path.relative_to(SRC)), lineno, int(match.group(1))))
    return found


class TestBaselineDoesNotGrow:
    def test_count_is_capped(self):
        entries = _baseline_entries()
        assert len(entries) <= MAX_BASELINED_FUNCTIONS, (
            f"{len(entries)} functions carry a C901 baseline, cap is "
            f"{MAX_BASELINED_FUNCTIONS}. Split the function instead of suppressing "
            f"the gate; if a suppression is genuinely warranted, raising the cap is "
            f"a reviewable decision, not a drive-by edit."
        )

    def test_cap_is_not_stale(self):
        """A cap far above the real count has stopped being a ratchet."""
        entries = _baseline_entries()
        assert len(entries) >= MAX_BASELINED_FUNCTIONS - 2, (
            f"only {len(entries)} baselines remain but the cap is still "
            f"{MAX_BASELINED_FUNCTIONS} -- lower MAX_BASELINED_FUNCTIONS to lock in "
            f"the progress."
        )

    def test_worst_case_does_not_regress(self):
        entries = _baseline_entries()
        worst = max((cc for _, _, cc in entries), default=0)
        assert worst <= MAX_BASELINED_COMPLEXITY, (
            f"a baselined function now records CC={worst}, above the recorded "
            f"worst case of {MAX_BASELINED_COMPLEXITY}. An already-too-complex "
            f"function got more complex."
        )


class TestBaselineIsWellFormed:
    def test_every_suppression_records_its_complexity(self):
        """A bare `# noqa: C901` hides how bad the function is."""
        bare: list[str] = []
        for path in SRC.rglob("*.py"):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "noqa" in line and "C901" in line and not _NOQA.search(line):
                    bare.append(f"{path.relative_to(SRC)}:{lineno}")
        assert bare == [], (
            "C901 suppressions must record the score, e.g. "
            "`# noqa: C901 -- baseline CC=22; split before extending`: " + ", ".join(bare)
        )

    def test_the_known_worst_offenders_are_present(self):
        """If these vanish, either they were fixed -- lower the cap -- or the
        annotation was dropped and the gate is now lying about them."""
        entries = {path: cc for path, _, cc in _baseline_entries()}
        for path, expected in (
            ("server/cli/commands/init.py", 49),
            ("server/config.py", 37),
            ("server/tools/batch/executor.py", 36),
        ):
            assert path in entries, f"{path} lost its C901 baseline annotation"
            assert entries[path] <= expected, f"{path} regressed past CC={expected}"
