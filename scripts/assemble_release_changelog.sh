#!/usr/bin/env bash
set -euo pipefail

# Folds changelog.d/ into CHANGELOG.md on the open release-please branch.
#
# release-please owns the version, the tag and the release PR; with
# `skip-changelog: true` it no longer writes the changelog body, so this runs
# right after it in the same job and commits the assembled section onto the same
# branch. Merging the release PR therefore lands both the version bump and the
# notes in one squash commit, exactly as before -- what changed is where the
# prose comes from (per-PR fragments instead of one contested block).
#
# Idempotent by design: release-please force-pushes its branch on every push to
# main, which drops this commit and restores the fragments. The next run redoes
# it. A branch that already carries its section is a no-op success.
#
# Inputs: GH_TOKEN (gh CLI), PUSH_TOKEN (git push; prefer the app token).

: "${GH_TOKEN:?GH_TOKEN must be set}"
PUSH_TOKEN="${PUSH_TOKEN:-$GH_TOKEN}"
REPO="${GITHUB_REPOSITORY:-mcp-hangar/mcp-hangar}"
BOT_BRANCH_PREFIX="release-please--"

branch=$(gh pr list --repo "$REPO" --state open --json headRefName \
  --jq "[.[] | select(.headRefName | startswith(\"${BOT_BRANCH_PREFIX}\")) | .headRefName][0] // \"\"")

if [ -z "$branch" ]; then
  echo "No open release-please PR. Nothing to assemble."
  exit 0
fi

echo "Release branch: ${branch}"
git fetch --force origin "${branch}:refs/remotes/origin/${branch}"
git checkout --force -B "$branch" "origin/${branch}"

version=$(python3 -c 'import json,pathlib;print(json.loads(pathlib.Path(".release-please-manifest.json").read_text())["."])')
echo "Manifest version: ${version}"

if ! python3 scripts/build_changelog.py assemble --version "$version"; then
  echo "::error::changelog assembly failed for ${version}"
  exit 1
fi

if git diff --quiet HEAD -- CHANGELOG.md changelog.d; then
  echo "Nothing to commit; CHANGELOG.md is already assembled for ${version}."
  exit 0
fi

git add -A CHANGELOG.md changelog.d
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -m "chore(release): assemble changelog for ${version}"

# Push with an explicit credential rather than the one checkout persisted. A
# push authenticated with the built-in GITHUB_TOKEN does not trigger workflows,
# and this commit becomes the PR head -- required checks would then be MISSING
# on it rather than red, which blocks the merge just as hard and looks like
# nothing is wrong. The app token does trigger them.
git push "https://x-access-token:${PUSH_TOKEN}@github.com/${REPO}.git" "HEAD:${branch}"

if [ "$PUSH_TOKEN" = "$GH_TOKEN" ] && [ -z "${RELEASE_BOT_APP_ID:-}" ]; then
  echo "::warning::Pushed with the built-in GITHUB_TOKEN, which does not re-trigger the release PR's required checks. Close/reopen the PR or push an empty commit to unblock the merge."
fi

echo "Assembled changelog for ${version} onto ${branch}."
