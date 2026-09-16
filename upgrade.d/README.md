# Upgrade-note fragments

A PR whose change a reader has to act on drops **one new file** here instead of
editing `UPGRADE.md`. At release time `scripts/promote_upgrade_notes.py` folds
them into a single `## Upgrade to <version>` section of `UPGRADE.md` and
deletes them.

This exists for the reason `changelog.d/` does. Every PR with an upgrade note
used to add a `## Next — <headline>` section at the top of `UPGRADE.md`, so any
two such PRs conflicted at the same spot, and each merge left most open PRs
dirty. A fragment is a new file, so there is nothing for git to merge.

## When

A change that breaks callers, removes public API, or changes what an existing
configuration does gets a note here, as well as its `changelog.d/` fragment. The
changelog says what changed; the note says how to move across it.

## Naming

```text
upgrade.d/<id>-<slug>.md
```

- `<id>` -- the issue or PR number, as in `changelog.d/`. It is the sort key:
  notes appear in the released section in file-name order.
- `<slug>` -- a few words, kebab-case. Reusing the `<id>-<slug>` of the PR's
  changelog fragment keeps the two easy to pair.

```text
upgrade.d/1409-member-health-failures.md
```

## Content

The first line is the note's heading at level three, and the rest of the file is
its body in markdown:

```markdown
### a group member is judged by its own health

A group member's circuit used to open on any failed call through the group ...
```

- The heading becomes a `###` subsection of `## Upgrade to <version>` exactly as
  written. Write it the way the headings already in `UPGRADE.md` read: what is
  now true.
- A subsection inside the note starts at `####`.
- No `## Next` heading and no version: the promoter adds the version.
- The prose rules of `UPGRADE.md` apply. Write for a reader upgrading, not for a
  reviewer: name the old and the new form, say what breaks and what to change.
- A fence that shows a key this release **removes** carries
  `<!-- config-check: skip -->` on the line above it. The docs site syncs
  `UPGRADE.md` verbatim, and its `check_config` gate reads every `yaml` fence
  there as configuration -- so an unmarked fence fails on the very key the note
  tells the reader to delete. One blank line may sit between the marker and the
  fence, which is what markdownlint wants around a fence. The marker covers the
  one fence beneath it: a note that shows two removals writes it twice. It marks
  an illustration of something being deleted, not a way to quiet a fence whose
  keys are simply not documented yet.

A removal note, whole:

````markdown
### a group's `circuit_breaker.reset_timeout_s` is removed

It never did anything: a breaker half-opens only when it is asked whether to let
a request through, and a group never asks. Delete it from every group:

<!-- config-check: skip -->

```yaml
mcp_servers:
  pool:
    mode: group
    circuit_breaker:
      failure_threshold: 10
      reset_timeout_s: 60   # delete this line
```
````

## Release

`scripts/assemble_release_changelog.sh` runs the promoter on the release-please
branch, in the same commit as the changelog assembly. Any `## Next` sections
still in `UPGRADE.md` come first, in file order, then the fragments by file name.
It is not something to run by hand on a feature branch.

A PR opened before this directory existed may still carry a `## Next —
<headline>` section at the top of `UPGRADE.md`. The promoter still reads it, so
the note ships either way; moving it into a fragment is what stops the PR
conflicting.

`python scripts/promote_upgrade_notes.py extract --version <version>` prints one
released section, which is what the docs repo's sync reads.
