# Project Board

## Overview

The MCP Hangar project uses a GitHub Projects v2 board for issue triage and lifecycle tracking.

Board URL: `https://github.com/orgs/mcp-hangar/projects/<N>` (set after first run of setup script).

## Custom fields

| Field | Type | Values |
|---|---|---|
| Priority | Single select | p0-critical, p1-high, p2-normal, p3-low |
| Scope | Single select | core, enterprise, cli, operator, helm, ui, observability, security, docs, deps, release, infra, tests, repo |
| Target Release | Text | Freeform, e.g. `1.1.0` |
| Estimate (LOC) | Number | Rough size hint |

## Status flow

```
Triage -> Backlog -> Ready -> In Progress -> In Review -> Done
                                  \-> Blocked -/
```

- **Triage**: newly opened issues land here.
- **Backlog**: accepted but not yet scheduled.
- **Ready**: scheduled for current cycle.
- **In Progress**: actively being worked on.
- **In Review**: PR open, awaiting review.
- **Blocked**: waiting on external dependency.
- **Done**: merged or closed.

## Built-in Project workflows

Configure these in the Project UI (Settings > Workflows):

1. **Item closed**: set Status = Done.
2. **Pull request merged**: set Status = Done.

The `.github/workflows/project-add.yml` workflow handles auto-adding new issues and PRs. The built-in workflows above handle status transitions.

## Token setup

The `project-add.yml` workflow requires a `PROJECT_AUTOMATION_TOKEN` secret and a `PROJECT_NUMBER` repository variable.

### Option A: Extend the release-bot App (recommended)

1. Go to the `mcp-hangar-release-bot` GitHub App settings.
2. Add `Organization > Projects: Read & write` permission.
3. Generate an installation token and store it as `PROJECT_AUTOMATION_TOKEN` in repository secrets.

### Option B: Fine-grained PAT

1. Create a fine-grained PAT scoped to the `mcp-hangar` org with `project: write`.
2. Store as `PROJECT_AUTOMATION_TOKEN` in repository secrets.

Do not use classic PATs scoped to a user account.

## Setup script

```bash
bash scripts/setup-gh-project.sh
OWNER=mcp-hangar PROJECT_TITLE="MCP Hangar" bash scripts/setup-gh-project.sh
```

The script is idempotent. Re-runs are no-ops for existing fields. Status field options must be configured manually in the Project UI (the gh CLI does not support modifying built-in field options).

After the first run, set the `PROJECT_NUMBER` repository variable:

```bash
gh variable set PROJECT_NUMBER --body "<N>"
```
