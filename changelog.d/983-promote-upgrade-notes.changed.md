**infra:** an upgrade note now gets the version it shipped in. `UPGRADE.md`
collects `## Next — ...` sections at PR time, next to the change that motivates
them, and nothing gave them a number: eight accumulated while 2.7.0, 2.8.0 and
2.9.0 shipped, so the changelog entries for those releases sent a reader to a
section headed "Next" -- which reads the same before and after the release it
describes. `assemble_release_changelog.sh` now folds them into one
`## Upgrade to X.Y.Z` section in the same commit as the changelog assembly, so
the release PR is also where a reviewer sees them together. That matters:
drafts written against different PRs contradict each other once they land in
one release, which is what the `builder()` note did. The 2.7.0-2.9.0 sections
are backfilled from the published guide
