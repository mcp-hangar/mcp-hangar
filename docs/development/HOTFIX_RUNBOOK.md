# Hotfix Runbook

## 1. When to use this runbook

- Confirmed security advisory with priority p0-critical or p1-high.
- Production regression in the last released version.
- Dependency CVE without an upstream patch available in `main`.

## 2. Branch from tag

```bash
git checkout -b hotfix/vX.Y.Z vX.Y.(Z-1)
```

Replace `vX.Y.(Z-1)` with the current release tag and `vX.Y.Z` with the next patch version.

## 3. Apply minimal fix

Cherry-pick from `main` if the fix commit exists there.
Otherwise hand-author the smallest possible diff. No refactoring.

## 4. Test minimal subset

```bash
pytest tests/security/ tests/<affected_module>/
```

Full suite is optional locally; CI runs it on the PR.

## 5. Update CHANGELOG

Add a new section `## [vX.Y.Z] - YYYY-MM-DD` above the existing `[Unreleased]` entry.
Use `### Security` for CVEs and security fixes. Use `### Fixed` for regressions.

## 6. Bump pyproject.toml

Patch version only. Update the `version` field in `pyproject.toml`.

## 7. Tag and push

```bash
git tag -s vX.Y.Z -m "Hotfix vX.Y.Z"
git push origin vX.Y.Z
```

No PR against `main`. Push directly to the hotfix branch and tag.

## 8. Release publishes automatically

[`.github/workflows/release.yml`](https://github.com/mcp-hangar/mcp-hangar/blob/main/.github/workflows/release.yml) consumes the tag and publishes to PyPI.

## 9. Forward-port to main (manual)

After the tag publishes, open a PR cherry-picking the hotfix commit(s) onto `main`.
Title: `chore(release): forward-port hotfix vX.Y.Z to main`.
Automation is deferred; see [GIT_FLOW.md Hotfix process](GIT_FLOW.md#hotfix-process).

## 10. Post-release

- Update the GitHub Security Advisory if applicable.
- Close related issues with a reference to `vX.Y.Z`.
