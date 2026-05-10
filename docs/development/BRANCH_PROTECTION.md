# Branch Protection

## Purpose

Branch protection on `main` ensures that every commit landing in the default branch has passed the full CI validation suite. It prevents direct pushes (except in emergencies during solo mode), enforces linear history via squash-merge, and requires conversation resolution before merge.

## Current configuration (solo mode)

- Required status checks (strict — branch must be up to date):
  - `pr-validation / required-check`
  - `enterprise-boundary`
  - `pr-title / validate`
  - `commitlint / lint`
  - `changelog / check`
  - `branch-name / validate`
  - `pr-body / validate`
- Required approving reviewers: 0
- Require code owner reviews: false
- Enforce admins: false
- Required linear history: true
- Allow force pushes: false
- Allow deletions: false
- Block creations: false
- Required conversation resolution: true

## Required status checks

| Check name | Workflow file | What it enforces |
|---|---|---|
| `pr-validation / required-check` | `ci.yml` | Paths-filter summary gate |
| `enterprise-boundary` | `security.yml` | No cross-boundary imports |
| `pr-title / validate` | `pr-title.yml` | Conventional Commits title |
| `commitlint / lint` | `commitlint.yml` | Per-commit message lint |
| `changelog / check` | `changelog-check.yml` | CHANGELOG entry present |
| `branch-name / validate` | `branch-name.yml` | Branch naming convention |
| `pr-body / validate` | `pr-body.yml` | PR body section structure |

## Solo vs community mode

| Setting | Solo | Community |
|---|---|---|
| Required approving reviewers | 0 | 1 |
| Require code owner reviews | false | true |
| Enforce admins | false | true |

Flip to community mode when there is at least one second maintainer. Until then `require_code_owner_reviews: true` would block all merges to CODEOWNERS-protected paths since GitHub does not allow self-approval.

## Applying the protection

```bash
bash scripts/setup-branch-protection.sh
bash scripts/setup-branch-protection.sh --mode community
bash scripts/setup-branch-protection.sh --dry-run
```

## Adding a new required check

1. Merge the workflow producing the check.
2. Wait for at least one PR to run it green.
3. Add the check name to the `contexts` array in `scripts/setup-branch-protection.sh`.
4. Re-run the script.

## Removing a required check

1. Comment out (do not delete) the check name in the script.
2. Re-run the script to apply the reduced list.
3. Delete or disable the workflow file in a separate PR.

## Emergency bypass

In solo mode (`enforce_admins: false`) the maintainer can push directly via `git push origin main` for true emergencies. This bypasses all checks — use only when CI itself is broken or a critical hotfix cannot wait.

In community mode (`enforce_admins: true`) bypass requires:

1. Temporarily set `enforce_admins: false` via GitHub UI.
2. Perform the emergency action.
3. Re-run `bash scripts/setup-branch-protection.sh --mode community` to restore.
