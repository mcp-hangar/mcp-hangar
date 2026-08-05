"""The changelog is assembled from per-PR fragments, so the fragments must hold.

`CHANGELOG.md` used to be written by every open PR at the same anchor -- one
`## [Unreleased]` block -- which made a conflict the normal outcome of having
two PRs open, and left prose orphaned under the wrong heading when release-please
inserted its own generated section above it.

`changelog.d/` removes the contention: a fragment is a new file, so git has
nothing to merge. What that trades away is the moment of truth. Nobody reads the
assembled file until release day, and a fragment that does not render is found
then -- on the release branch, by a bot, with the release waiting. These tests
move that failure to the PR that introduces it.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRAGMENT_DIR = ROOT / "changelog.d"

sys.path.insert(0, str(ROOT / "scripts"))
from build_changelog import (  # noqa: E402
    SECTIONS,
    Fragment,
    FragmentError,
    insert_section,
    parse_fragment,
    render_bullet,
    render_section,
)

PREAMBLE = """# Changelog

All notable changes to this project will be documented in this file.

"""


def _fragment(**overrides: object) -> Fragment:
    defaults = {
        "path": pathlib.Path("changelog.d/1-x.fixed.md"),
        "kind": "fixed",
        "ident": 1,
        "stem": "1-x",
        "text": "**core:** a thing",
        "pr": 1,
    }
    defaults.update(overrides)
    return Fragment(**defaults)  # type: ignore[arg-type]


class TestPendingFragments:
    """The standing gate: whatever is in changelog.d/ right now must render."""

    def test_every_pending_fragment_parses(self) -> None:
        for path in sorted(FRAGMENT_DIR.glob("*.md")):
            if path.name in {"README.md", "_summary.md"}:
                continue
            parse_fragment(path)

    def test_readme_documents_every_kind(self) -> None:
        # The kinds are the section names; a kind the README omits is a kind
        # nobody will use, and one it invents fails the gate at PR time.
        readme = (FRAGMENT_DIR / "README.md").read_text(encoding="utf-8")
        for kind in SECTIONS:
            assert f"`{kind}`" in readme, f"changelog.d/README.md does not document '{kind}'"


class TestParsing:
    def test_rejects_an_unknown_kind(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "12-thing.improved.md"
        path.write_text("**core:** a thing", encoding="utf-8")
        with pytest.raises(FragmentError, match="unknown kind"):
            parse_fragment(path)

    def test_rejects_an_empty_fragment(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "12-thing.fixed.md"
        path.write_text("\n  \n", encoding="utf-8")
        with pytest.raises(FragmentError, match="empty"):
            parse_fragment(path)

    def test_rejects_a_pre_bulleted_fragment(self, tmp_path: pathlib.Path) -> None:
        # The assembler adds the bullet. A fragment that brings its own renders
        # as `- - **core:** ...`, which nothing would catch before release day.
        path = tmp_path / "12-thing.fixed.md"
        path.write_text("- **core:** a thing", encoding="utf-8")
        with pytest.raises(FragmentError, match="no leading bullet"):
            parse_fragment(path)

    def test_accepts_a_fragment_without_a_leading_id(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "thing.fixed.md"
        path.write_text("**core:** a thing", encoding="utf-8")
        fragment = parse_fragment(path)
        assert fragment.ident is None
        assert fragment.kind == "fixed"


class TestRendering:
    def test_bullet_carries_the_pr_link(self) -> None:
        bullet = render_bullet(_fragment(pr=748))
        assert bullet.startswith("- **core:** a thing (")
        assert "/pull/748)" in bullet

    def test_bullet_without_a_pr_number_has_no_empty_link(self) -> None:
        assert render_bullet(_fragment(pr=None)) == "- **core:** a thing"

    def test_continuation_lines_stay_inside_the_bullet(self) -> None:
        bullet = render_bullet(_fragment(text="first\n\nsecond", pr=None))
        assert bullet == "- first\n\n  second"

    def test_sections_render_in_keep_a_changelog_order(self) -> None:
        fragments = [
            _fragment(kind="security", ident=1, stem="1-s", text="s"),
            _fragment(kind="added", ident=2, stem="2-a", text="a"),
            _fragment(kind="fixed", ident=3, stem="3-f", text="f"),
        ]
        section = render_section("2.4.0", fragments, date="2026-08-04", prev_tag="v2.3.0", summary=None)
        assert [line for line in section.splitlines() if line.startswith("### ")] == [
            "### Added",
            "### Fixed",
            "### Security",
        ]

    def test_entries_are_newest_first_and_unnumbered_ones_last(self) -> None:
        fragments = [
            _fragment(ident=700, stem="700-a", text="older"),
            _fragment(ident=None, stem="zz", text="unnumbered", pr=None),
            _fragment(ident=749, stem="749-b", text="newer"),
        ]
        section = render_section("2.4.0", fragments, date="2026-08-04", prev_tag="v2.3.0", summary=None)
        bullets = [line for line in section.splitlines() if line.startswith("- ")]
        assert [b.split(" ")[1] for b in bullets] == ["newer", "older", "unnumbered"]

    def test_heading_carries_the_compare_link(self) -> None:
        section = render_section("2.4.0", [_fragment()], date="2026-08-04", prev_tag="v2.3.0", summary=None)
        assert section.splitlines()[0] == (
            "## [2.4.0](https://github.com/mcp-hangar/mcp-hangar/compare/v2.3.0...v2.4.0) (2026-08-04)"
        )

    def test_first_ever_release_links_to_the_tag_instead_of_a_compare(self) -> None:
        section = render_section("0.1.0", [_fragment()], date="2026-08-04", prev_tag=None, summary=None)
        assert "/releases/tag/v0.1.0)" in section.splitlines()[0]

    def test_summary_lands_above_the_sections(self) -> None:
        section = render_section("2.4.0", [_fragment()], date="2026-08-04", prev_tag="v2.3.0", summary="A theme.\n")
        lines = section.splitlines()
        assert lines[2] == "A theme."
        assert lines.index("A theme.") < lines.index("### Fixed")


class TestInsertion:
    def test_new_section_goes_above_the_newest_existing_one(self) -> None:
        changelog = PREAMBLE + "## [2.3.0](x) (2026-08-04)\n\n### Fixed\n\n- old\n"
        result = insert_section(changelog, "## [2.4.0](y) (2026-08-05)\n\n### Fixed\n\n- new\n")
        headings = [line for line in result.splitlines() if line.startswith("## [")]
        assert headings == ["## [2.4.0](y) (2026-08-05)", "## [2.3.0](x) (2026-08-04)"]

    def test_a_blank_line_separates_it_from_the_previous_release(self) -> None:
        # `splitlines()` drops a trailing newline, which ran the last bullet of
        # the new section straight into the previous version's heading.
        changelog = PREAMBLE + "## [2.3.0](x) (2026-08-04)\n\n- old\n"
        result = insert_section(changelog, "## [2.4.0](y) (2026-08-05)\n\n- new\n")
        lines = result.splitlines()
        assert lines[lines.index("## [2.3.0](x) (2026-08-04)") - 1] == ""

    def test_an_empty_unreleased_heading_is_dropped(self) -> None:
        # Unreleased work lives in changelog.d/ now. An empty heading left in the
        # file would be a second place to look for it.
        changelog = PREAMBLE + "## [Unreleased]\n\n## [2.3.0](x) (2026-08-04)\n\n- old\n"
        result = insert_section(changelog, "## [2.4.0](y) (2026-08-05)\n\n- new\n")
        assert "Unreleased" not in result

    def test_the_unreleased_reference_link_goes_with_it(self) -> None:
        changelog = (
            PREAMBLE
            + "## [Unreleased]\n\n## [2.3.0](x) (2026-08-04)\n\n- old\n\n"
            + "[Unreleased]: https://github.com/mcp-hangar/mcp-hangar/compare/v2.3.0...HEAD\n"
            + "[2.3.0]: https://github.com/mcp-hangar/mcp-hangar/compare/v2.2.1...v2.3.0\n"
        )
        result = insert_section(changelog, "## [2.4.0](y) (2026-08-05)\n\n- new\n")
        assert "[Unreleased]:" not in result
        assert "[2.3.0]: https://" in result

    def test_it_refuses_to_discard_prose_still_under_unreleased(self) -> None:
        # The migration moves those entries into fragments. Silently dropping
        # them would delete written history at the least reversible moment.
        changelog = PREAMBLE + "## [Unreleased]\n\n### Fixed\n\n- pending\n\n## [2.3.0](x) (d)\n"
        with pytest.raises(FragmentError, match="still has content"):
            insert_section(changelog, "## [2.4.0](y) (e)\n\n- new\n")

    def test_a_changelog_with_no_versions_yet_still_gets_the_section(self) -> None:
        result = insert_section(PREAMBLE, "## [0.1.0](y) (2026-08-05)\n\n- new\n")
        assert result.rstrip().endswith("- new")
        assert result.startswith("# Changelog")
