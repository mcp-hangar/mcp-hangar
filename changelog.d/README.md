# Changelog fragments

Every non-trivial PR drops **one new file** here instead of editing
`CHANGELOG.md`. At release time `scripts/build_changelog.py` folds them into a
single version section and deletes them.

This exists for one reason: `CHANGELOG.md` was a single anchor that every open
PR wrote to, so two PRs open at once always conflicted. A fragment is a new
file, so there is nothing for git to merge.

## Naming

```text
changelog.d/<id>-<slug>.<kind>.md
```

- `<id>` -- the issue or PR number. It is a sort key and a fallback link only:
  the PR link is normally read from the squash commit that added the file, so
  guessing the number wrong (or omitting it) does not produce a wrong link.
- `<slug>` -- a few words, kebab-case.
- `<kind>` -- one of `added`, `changed`, `deprecated`, `removed`, `fixed`,
  `security`. These are the Keep a Changelog sections and become the `###`
  headings, in that order.

```text
changelog.d/748-dead-symbol-facade.fixed.md
changelog.d/749-drop-event-sourced-pair.removed.md
```

## Content

The file holds the entry text and nothing else -- no leading bullet, no heading,
no PR link. The assembler adds all three. Start with the Conventional Commit
scope in bold, matching the existing entries:

```markdown
**core:** the dead-symbol gate could not see through a package facade.
`_referenced_names` counted every import alias as a use, so
`from .module import Thing` in an `__init__.py` marked `Thing` referenced even
when nothing in `src/` or `tests/` imported it
```

Write it for a reader upgrading, not for a reviewer: what changed, what breaks,
what to do about it. A multi-paragraph fragment is fine -- continuation lines
are indented into the bullet automatically.

A breaking change is described here **and** gets a section in `UPGRADE.md`
naming the old and new form. The commit itself never carries `!` or a
`BREAKING CHANGE:` footer (see `AGENTS.md`).

## Release summary

An optional `changelog.d/_summary.md` becomes the intro paragraph above the
sections, the way v2.3.0 has one. Use it when a release has a theme worth
stating in two sentences; skip it otherwise.

## Trivial changes

`chore(deps)`, `ci`, `style`, `test` and pure `docs` PRs need no fragment. The
`changelog / check` gate only fires on changes under `src/`, `pyproject.toml`
or `packages/`. Two things bypass it: the `skip-changelog` label, and a
Conventional Commit title in a dependency scope (`chore(deps)`, `ci(deps)`,
`build(deps-dev)`, ...) -- a Python bump edits `pyproject.toml`, so without
that every dependabot PR failed a required check. A bump that IS worth an
entry may still add a fragment; only the requirement is lifted.

## Commands

```bash
make changelog-check                    # validate every pending fragment
make changelog-preview VERSION=2.4.0    # render the section to stdout
```

Assembly runs automatically on the release-please branch; it is not something
to do by hand on a feature branch.
