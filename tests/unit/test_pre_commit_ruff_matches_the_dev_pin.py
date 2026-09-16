"""The pre-commit ruff and the ruff CI installs must be one version.

CI installs ruff from the `ruff==` pin in the dev extra; pre-commit fetches its
own copy at the `rev` it pins. Two pins are two formatters: a rule that fires in
the hook is invisible in CI, or the hook rewrites a file CI then rejects. The
repo has been here before -- `RUFF_VERSION` in ci-core.yml sat at 0.14.13 while
the dev dependency floated, and two UP042 findings went unseen for exactly that
(#702). The hook's own pin was the half left behind, at 0.14.13 against a dev
extra of 0.16.7 (#1496).

Dependabot bumps both ecosystems, but in separate pull requests and without ever
comparing them, so it cannot be what keeps them equal. This test is: bump one
pin and it fails until the other follows.

The second test guards the claim rather than the version. Ruff sorts imports
only when `I` is in `select`, and it is not; a hook named "isort" that sorts
nothing is worse than no hook, because it is read as coverage. If `I` is ever
enabled, the names may say isort again -- and until then they may not.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"
PYPROJECT = ROOT / "pyproject.toml"
CI_CORE = ROOT / ".github" / "workflows" / "ci-core.yml"

_DEV_PIN = re.compile(r'"ruff==([0-9][^"]*)"')
_HOOK_REV = re.compile(r"repo:\s*https://github\.com/astral-sh/ruff-pre-commit\s*\n\s*rev:\s*v([0-9][^\s]*)")
_SELECT = re.compile(r"^select\s*=\s*\[([^\]]*)\]", re.MULTILINE)


def _one(pattern: re.Pattern[str], path: pathlib.Path, what: str) -> str:
    found = pattern.findall(path.read_text(encoding="utf-8"))
    assert len(found) == 1, f"expected exactly one {what} in {path.name}, found {found}"
    return found[0]


def test_the_hook_pins_the_ruff_the_dev_extra_pins() -> None:
    dev_pin = _one(_DEV_PIN, PYPROJECT, "`ruff==` pin")
    hook_rev = _one(_HOOK_REV, PRE_COMMIT, "ruff-pre-commit `rev`")

    assert hook_rev == dev_pin, (
        f"pre-commit runs ruff {hook_rev}, CI runs {dev_pin}. Set `rev: v{dev_pin}` in "
        ".pre-commit-config.yaml, or move the dev extra to the hook's version -- but not neither."
    )


def test_nothing_claims_import_sorting_while_i_is_unselected() -> None:
    selected = [rule.strip().strip('"') for rule in _one(_SELECT, PYPROJECT, "ruff `select`").split(",")]

    if "I" in selected:
        return

    for path in (PRE_COMMIT, CI_CORE):
        text = path.read_text(encoding="utf-8").lower()
        assert "isort" not in text, (
            f"{path.name} mentions isort, but `I` is not in ruff's `select`, so nothing sorts imports. "
            "Enable `I` or drop the claim (#1496)."
        )
