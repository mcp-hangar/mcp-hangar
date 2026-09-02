#!/usr/bin/env bash
set -euo pipefail

# Start the checks on a release PR whose head was pushed with GITHUB_TOKEN.
#
# A push authenticated with the built-in `GITHUB_TOKEN` does not trigger
# workflows -- by design, to stop a run from re-triggering itself. When the
# changelog assembly falls back to it, the commit it pushes becomes the release
# PR's head and carries no runs at all, so every required check stays in
# "expected" and the merge is blocked for good: `--admin` does not clear a
# ruleset requirement, and the error message points at branch protection rather
# than at the missing runs (#1180, seen on the 2.17.1 release PR).
#
# Close/reopen fires `pull_request: reopened` against the head that is already
# there, so the checks run and no history is rewritten.
#
# Its own file so the recovery can be exercised without standing up a release:
# `tests/ci/test_release_pr_checks_are_refired.py` runs it against a stub `gh`.
#
# Usage: refire_release_pr_checks.sh <branch>
# Inputs: GH_TOKEN (gh CLI), GITHUB_REPOSITORY (defaults to the core repo).

branch="${1:?branch is required}"
repo="${GITHUB_REPOSITORY:-mcp-hangar/mcp-hangar}"

pr=$(gh pr list --repo "$repo" --state open --head "$branch" --json number --jq '.[0].number // ""')

if [ -z "$pr" ]; then
  echo "::warning::No open PR for ${branch}; its checks must be started by hand."
  exit 0
fi

# Never fatal. An unmergeable release PR is recoverable by hand, and failing
# here would also discard the notes the caller just assembled onto it.
if gh pr close "$pr" --repo "$repo" && gh pr reopen "$pr" --repo "$repo"; then
  echo "Reopened PR #${pr} to start its checks."
else
  echo "::warning::Could not reopen PR #${pr}; close and reopen it by hand to start its checks."
fi
