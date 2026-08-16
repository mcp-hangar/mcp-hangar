"""An upgrade note must not ship still headed "Next".

Eight `## Next` sections accumulated in `UPGRADE.md` while 2.7.0, 2.8.0 and
2.9.0 shipped, so the changelog for those releases pointed a reader at a section
that could not say which release it described (#983). Promotion now runs from
`assemble_release_changelog.sh`, which means it runs unattended on a release
branch -- and release-please force-pushes that branch, so it reruns on a tree it
has already rewritten. Both properties are tested here rather than discovered on
a release day.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT / "scripts"))
from promote_upgrade_notes import extract, promote, split_drafts  # noqa: E402

GUIDE = """# Upgrading MCP Hangar

## Next — `hangar_load` needs `uvx` on PATH

Body of the first draft.

## Next — the factory stack is gone

Body of the second draft.

## Upgrade to 2.9.0

### Something older

Older body.
"""


def write(tmp_path: pathlib.Path, text: str = GUIDE) -> pathlib.Path:
    path = tmp_path / "UPGRADE.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_draft_becomes_a_subsection_of_the_released_version(tmp_path):
    path = write(tmp_path)
    assert promote(path, "2.10.0") == 0

    text = path.read_text()
    assert "## Upgrade to 2.10.0" in text
    assert "### `hangar_load` needs `uvx` on PATH" in text
    assert "### the factory stack is gone" in text
    assert "## Next" not in text


def test_the_new_section_goes_above_the_history(tmp_path):
    path = write(tmp_path)
    promote(path, "2.10.0")
    text = path.read_text()
    assert text.index("## Upgrade to 2.10.0") < text.index("## Upgrade to 2.9.0")
    assert text.startswith("# Upgrading MCP Hangar")


def test_promoting_twice_changes_nothing(tmp_path):
    """release-please force-pushes its branch, so this reruns on its own output."""
    path = write(tmp_path)
    promote(path, "2.10.0")
    once = path.read_text()

    assert promote(path, "2.10.0") == 0
    assert path.read_text() == once


def test_a_release_with_no_drafts_is_not_an_error(tmp_path):
    """Most releases carry no upgrade note, and must not fail the release job."""
    path = write(tmp_path, "# Upgrading MCP Hangar\n\n## Upgrade to 2.9.0\n\nBody.\n")
    assert promote(path, "2.10.0") == 0
    assert "## Upgrade to 2.10.0" not in path.read_text()


def test_the_history_below_is_left_alone(tmp_path):
    path = write(tmp_path)
    promote(path, "2.10.0")
    assert "### Something older\n\nOlder body." in path.read_text()


def test_extract_prints_one_section_for_the_docs_sync(tmp_path, capsys):
    path = write(tmp_path)
    promote(path, "2.10.0")
    capsys.readouterr()

    assert extract(path, "2.10.0") == 0
    printed = capsys.readouterr().out
    assert printed.startswith("## Upgrade to 2.10.0")
    assert "the factory stack is gone" in printed
    # The next version's section must not bleed into it.
    assert "Something older" not in printed


def test_extract_fails_loudly_on_a_version_that_is_not_there(tmp_path):
    assert extract(write(tmp_path), "9.9.9") == 1


@pytest.mark.parametrize("dash", ["—", "-"])
def test_both_dashes_are_recognised(dash):
    """The drafts in this repo are written with an em dash; a hyphen is the
    thing an author reaches for when the em dash is inconvenient, and a draft
    the promoter cannot see is a draft that ships headed "Next"."""
    drafts, _ = split_drafts(f"# T\n\n## Next {dash} a headline\n\nBody.\n")
    assert [h for h, _ in drafts] == ["a headline"]


def test_the_real_guide_claims_no_version_past_the_released_one():
    """Promotion runs on a release branch, where the version is whatever the
    manifest says. Running it anywhere else stamps a section for a version that
    does not exist -- and a section headed `## Upgrade to 3.4.0` is worse than
    one headed `Next`, because it reads as shipped."""
    text = (ROOT / "UPGRADE.md").read_text(encoding="utf-8")
    released = (ROOT / "pyproject.toml").read_text(encoding="utf-8").split('version = "', 1)[1].split('"', 1)[0]
    released_key = tuple(int(p) for p in released.split(".")[:3])

    claimed = [tuple(int(p) for p in m) for m in re.findall(r"^## Upgrade to (\d+)\.(\d+)\.(\d+)", text, re.M)]
    ahead = sorted(v for v in claimed if v > released_key)
    assert not ahead, f"UPGRADE.md claims {ahead}, released is {released_key}"
