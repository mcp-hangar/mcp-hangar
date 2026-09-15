"""An upgrade note must not ship still headed "Next".

Eight `## Next` sections accumulated in `UPGRADE.md` while 2.7.0, 2.8.0 and
2.9.0 shipped, so the changelog for those releases pointed a reader at a section
that could not say which release it described (#983). Promotion now runs from
`assemble_release_changelog.sh`, which means it runs unattended on a release
branch -- and release-please force-pushes that branch, so it reruns on a tree it
has already rewritten. Both properties are tested here rather than discovered on
a release day.

A note is now a fragment in `upgrade.d/`, because every PR writing a `## Next`
section at the top of `UPGRADE.md` conflicted with every other one. The legacy
sections are still promoted, so a PR opened before the move still ships its
note; both sources, and the two together, are tested below.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT / "scripts"))
from promote_upgrade_notes import (  # noqa: E402
    DRAFT_RE,
    FragmentError,
    extract,
    find_section,
    promote,
    read_fragments,
    split_drafts,
)

GUIDE = """# Upgrading MCP Hangar

## Next — `hangar_load` needs `uvx` on PATH

Body of the first draft.

## Next — the factory stack is gone

Body of the second draft.

## Upgrade to 2.9.0

### Something older

Older body.
"""

HISTORY_ONLY = """# Upgrading MCP Hangar

## Upgrade to 2.9.0

### Something older

Older body.
"""

README = "# Upgrade-note fragments\n\nDocuments the format; not a note.\n"

# Written out of order on purpose: promotion orders by file name.
FRAGMENTS = {
    "20-second.md": "### the second note\n\nBody of the second note.\n",
    "10-first.md": "### the first note\n\nBody of the first note.\n\n#### A detail\n\nMore.\n",
}
FIRST_BLOCK = "### the first note\n\nBody of the first note.\n\n#### A detail\n\nMore.\n"
SECOND_BLOCK = "### the second note\n\nBody of the second note.\n"


def write(tmp_path: pathlib.Path, text: str = GUIDE) -> pathlib.Path:
    path = tmp_path / "UPGRADE.md"
    path.write_text(text, encoding="utf-8")
    return path


def add_fragments(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    directory = tmp_path / "upgrade.d"
    directory.mkdir(exist_ok=True)
    (directory / "README.md").write_text(README, encoding="utf-8")
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def names_in(directory: pathlib.Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


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


# -- fragments in upgrade.d/ ---------------------------------------------------


def test_fragments_alone_become_the_released_section_in_file_name_order(tmp_path):
    path = write(tmp_path, HISTORY_ONLY)
    add_fragments(tmp_path, FRAGMENTS)
    assert promote(path, "2.10.0") == 0

    text = path.read_text()
    assert text.startswith("# Upgrading MCP Hangar\n\n## Upgrade to 2.10.0\n\n### the first note\n")
    assert text.count(FIRST_BLOCK) == 1
    assert text.count(SECOND_BLOCK) == 1
    assert text.index(FIRST_BLOCK) < text.index(SECOND_BLOCK) < text.index("## Upgrade to 2.9.0")
    assert text.endswith("## Upgrade to 2.9.0\n\n### Something older\n\nOlder body.\n")


def test_promoted_fragments_are_deleted_and_the_readme_is_kept(tmp_path):
    path = write(tmp_path, HISTORY_ONLY)
    directory = add_fragments(tmp_path, FRAGMENTS)
    promote(path, "2.10.0")
    assert names_in(directory) == ["README.md"]


def test_the_readme_is_not_a_note(tmp_path):
    path = write(tmp_path, HISTORY_ONLY)
    directory = add_fragments(tmp_path, {})
    assert promote(path, "2.10.0") == 0
    assert path.read_text() == HISTORY_ONLY
    assert names_in(directory) == ["README.md"]


def test_legacy_sections_alone_still_promote(tmp_path):
    """A PR opened before `upgrade.d/` existed still ships its `## Next` note."""
    path = write(tmp_path)
    directory = add_fragments(tmp_path, {})
    assert promote(path, "2.10.0") == 0

    text = path.read_text()
    assert "### `hangar_load` needs `uvx` on PATH\n\nBody of the first draft.\n" in text
    assert "### the factory stack is gone\n\nBody of the second draft.\n" in text
    assert not DRAFT_RE.search(text)
    assert names_in(directory) == ["README.md"]


def test_legacy_sections_and_fragments_fold_into_one_section(tmp_path):
    path = write(tmp_path)
    directory = add_fragments(tmp_path, FRAGMENTS)
    assert promote(path, "2.10.0") == 0

    text = path.read_text()
    assert text.count("## Upgrade to 2.10.0") == 1
    headings = re.findall(r"^### (.+)$", find_section(text, "2.10.0") or "", re.M)
    assert headings == [
        "`hangar_load` needs `uvx` on PATH",
        "the factory stack is gone",
        "the first note",
        "the second note",
    ]
    assert not DRAFT_RE.search(text)
    assert names_in(directory) == ["README.md"]


def test_a_rerun_after_the_fragments_were_consumed_changes_nothing(tmp_path):
    path = write(tmp_path)
    directory = add_fragments(tmp_path, FRAGMENTS)
    promote(path, "2.10.0")
    once = path.read_text()

    assert promote(path, "2.10.0") == 0
    assert path.read_text() == once
    assert names_in(directory) == ["README.md"]


def test_a_note_pending_against_a_written_section_is_left_where_it_is(tmp_path, capsys):
    """Folding it in could stamp it with a version it does not ship in; deleting
    it would lose it. It stays, and the release log says so."""
    path = write(tmp_path, HISTORY_ONLY)
    directory = add_fragments(tmp_path, FRAGMENTS)
    assert promote(path, "2.9.0") == 0

    assert path.read_text() == HISTORY_ONLY
    assert names_in(directory) == ["10-first.md", "20-second.md", "README.md"]
    assert "::warning::2 upgrade note(s) left pending" in capsys.readouterr().out


@pytest.mark.parametrize(
    "bad",
    [
        "## the wrong level\n\nBody.\n",
        "Body with no heading at all.\n",
        "\n### a blank line first\n\nBody.\n",
        "###\n\nBody under an empty heading.\n",
        "### a heading and nothing else\n",
    ],
)
def test_a_malformed_fragment_fails_the_release_and_changes_nothing(tmp_path, bad):
    """Guessing what a malformed fragment meant would ship a note that reads
    wrong; failing leaves the guide and every fragment as they were."""
    path = write(tmp_path)
    directory = add_fragments(tmp_path, {**FRAGMENTS, "15-bad.md": bad})

    with pytest.raises(FragmentError):
        read_fragments(directory)
    assert promote(path, "2.10.0") == 1
    assert path.read_text() == GUIDE
    assert names_in(directory) == ["10-first.md", "15-bad.md", "20-second.md", "README.md"]


# -- the real tree -------------------------------------------------------------

FRAGMENT_NAME_RE = re.compile(r"^\d+-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")


def test_every_pending_fragment_in_the_repo_is_well_formed():
    """The standing gate: a fragment that cannot be promoted fails its own PR,
    not the release job that meets it weeks later."""
    directory = ROOT / "upgrade.d"
    assert (directory / "README.md").is_file()

    misnamed = [p.name for p in directory.glob("*.md") if p.name != "README.md" and not FRAGMENT_NAME_RE.match(p.name)]
    assert not misnamed, f"upgrade.d/ fragments must be named <id>-<slug>.md: {misnamed}"
    read_fragments(directory)


def test_a_release_dry_run_folds_every_pending_note_exactly_once(tmp_path):
    """Run the release's promotion on a copy of the real tree, at a version that
    does not exist, and check nothing is lost, doubled or left behind."""
    guide = tmp_path / "UPGRADE.md"
    shutil.copy(ROOT / "UPGRADE.md", guide)
    shutil.copytree(ROOT / "upgrade.d", tmp_path / "upgrade.d")
    before = guide.read_text(encoding="utf-8")

    drafts, _ = split_drafts(before)
    pending = drafts + [(headline, body) for _, headline, body in read_fragments(tmp_path / "upgrade.d")]
    history = before[before.index("## Upgrade to ") :]

    assert promote(guide, "99.0.0") == 0
    after = guide.read_text(encoding="utf-8")
    section = find_section(after, "99.0.0")

    if not pending:
        assert section is None
        assert after == before
        return

    assert section is not None
    for headline, body in pending:
        assert section.count(f"### {headline}\n\n{body}\n") == 1, headline
    assert after.startswith("# Upgrading MCP Hangar\n\n## Upgrade to 99.0.0\n")
    assert after.endswith(history)
    assert not DRAFT_RE.search(after)
    assert names_in(tmp_path / "upgrade.d") == ["README.md"]


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
