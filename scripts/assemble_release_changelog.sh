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

# Take the assembler OUT of the tree before switching to the release branch.
#
# The checkout below replaces the whole working tree, and the release branch is
# not guaranteed to contain this mechanism: release-please only rebuilds its
# branch when the commit set changes the release it proposes, so right after
# this landed the branch still pointed at the previous main. The step checked
# it out, deleted `scripts/build_changelog.py` from under itself, and failed
# trying to run a file that was no longer there (run 30955840864). A snapshot
# costs one `cp` and makes the step independent of what the branch happens to
# carry.
#
# Checking out only the paths (`git checkout "$branch" -- CHANGELOG.md`) would
# keep the tooling but leave HEAD on main, and this step has to COMMIT onto the
# release branch.
assembler="${RUNNER_TEMP:-/tmp}/build_changelog.py"
cp scripts/build_changelog.py "$assembler"

# Same snapshot reason as above: the release branch is not guaranteed to carry
# this script either.
promoter="${RUNNER_TEMP:-/tmp}/promote_upgrade_notes.py"
cp scripts/promote_upgrade_notes.py "$promoter"

# And the recovery, for the same reason: it runs after the checkout, and the
# release branch predates it.
refire="${RUNNER_TEMP:-/tmp}/refire_release_pr_checks.sh"
cp scripts/refire_release_pr_checks.sh "$refire"

git fetch --force origin "${branch}:refs/remotes/origin/${branch}"

# Refuse to write onto a release branch that does not contain the commit this
# run was triggered by.
#
# release-please rebuilds its branch only when the commit set changes the
# release it proposes, so a push that leaves the version alone leaves a branch
# behind main -- carrying, among other things, whatever CHANGELOG.md main has
# since moved on from. That is what produced the conflict on #739: main gained
# a 2.4.0 section while the branch still held release-please's own generated
# one. Assembling onto that tree would write notes destined to conflict, so it
# waits for the rebuild instead and says why.
if ! git merge-base --is-ancestor "${GITHUB_SHA:-HEAD}" "origin/${branch}"; then
  echo "::warning::${branch} does not contain ${GITHUB_SHA:-HEAD}; it is behind main. Skipping assembly until release-please rebuilds it."
  exit 0
fi

git checkout --force -B "$branch" "origin/${branch}"

version=$(python3 -c 'import json,pathlib;print(json.loads(pathlib.Path(".release-please-manifest.json").read_text())["."])')
echo "Manifest version: ${version}"

if ! python3 "$assembler" assemble --version "$version"; then
  echo "::error::changelog assembly failed for ${version}"
  exit 1
fi

# Give the release's upgrade notes their version, in the same commit.
#
# `UPGRADE.md` collects `## Next — ...` sections at PR time, next to the change
# that motivated them, and nothing used to give them a number: eight accumulated
# while 2.7.0, 2.8.0 and 2.9.0 shipped, so the changelog for those releases sent
# a reader to a section headed "Next" (#983). Folding them here also means the
# release PR is where a reviewer sees them together -- drafts written against
# different PRs contradict each other once they land in one release.
if ! python3 "$promoter" promote --version "$version"; then
  echo "::error::upgrade-note promotion failed for ${version}"
  exit 1
fi

if git diff --quiet HEAD -- CHANGELOG.md changelog.d UPGRADE.md; then
  echo "Nothing to commit; CHANGELOG.md is already assembled for ${version}."
  exit 0
fi

git add -A CHANGELOG.md changelog.d UPGRADE.md

# Commit as the identity that PUSHES this, not as a different bot.
#
# The organisation requires approval to run workflows for "first-time
# contributors" -- anyone who has never had a commit or pull request merged
# into this repository -- and it checks the ACTOR of the pull request event,
# not only the author. Signing this commit as `github-actions[bot]` made that
# the actor, and `github-actions[bot]` never has anything merged here: the
# assembly commit lives on the release branch and the squash into main is
# attributed to the pull request's author, the release app. So every release
# PR's checks sat in `action_required` waiting for a human, while the merge was
# refused with "13 of 13 required status checks are expected" (#1184).
#
# The release app is not a first-time contributor -- it authors and merges a
# release PR every time -- so committing as the app is what makes the runs
# start on their own. It is also just true: the app's token is what pushes the
# commit two lines below.
COMMIT_NAME="${RELEASE_BOT_NAME:-mcp-hangar-release-bot[bot]}"
COMMIT_EMAIL="${RELEASE_BOT_EMAIL:-283430731+mcp-hangar-release-bot[bot]@users.noreply.github.com}"

# ... unless there is no app token, in which case `github-actions[bot]` is who
# actually pushed and claiming otherwise would be a lie in the history. That
# path is the one the recovery below exists for.
if [ "$PUSH_TOKEN" = "$GH_TOKEN" ]; then
  COMMIT_NAME="github-actions[bot]"
  COMMIT_EMAIL="41898282+github-actions[bot]@users.noreply.github.com"
fi

git -c user.name="$COMMIT_NAME" \
    -c user.email="$COMMIT_EMAIL" \
    commit -m "chore(release): assemble changelog and upgrade notes for ${version}"

# Push with an explicit credential rather than the one checkout persisted. A
# push authenticated with the built-in GITHUB_TOKEN does not trigger workflows,
# and this commit becomes the PR head -- required checks would then be MISSING
# on it rather than red, which blocks the merge just as hard and looks like
# nothing is wrong. The app token does trigger them.
git push "https://x-access-token:${PUSH_TOKEN}@github.com/${REPO}.git" "HEAD:${branch}"

# Recover on what is observable, not on a theory about why.
#
# The first version of this guessed at the cause: it compared PUSH_TOKEN to
# GH_TOKEN, on the reasoning that a push made with the built-in GITHUB_TOKEN
# does not trigger workflows. That reasoning is sound and the condition was
# still wrong -- on the run it was written for, the app token WAS available, so
# the comparison was false and the recovery never ran. The release PR sat with
# 13 required checks "expected" anyway (#1173).
#
# What actually happened, from the API rather than from inference: the runs
# existed, they belonged to `github-actions[bot]`, and they produced nothing --
# ten runs with zero jobs and `conclusion: failure` on one head, ten runs in
# `action_required` on the next. Re-firing the same workflows as a human actor
# produced checks immediately, both times. So the condition worth testing is
# not which credential pushed, but whether the head ended up with any check
# runs at all -- which is the thing that blocks the merge, and the thing that
# is true whatever the underlying reason turns out to be (#1184).
#
# The grace period is for the ordinary case: a head that has just been pushed
# has no checks for a few seconds either way.
sleep "${CHECK_GRACE_S:-45}"

head_sha=$(git rev-parse HEAD)

# CHECK RUNS, not workflow runs. The two differ exactly where it matters: on
# the head this was written for, ten workflow runs existed and contributed no
# checks at all -- zero jobs and `conclusion: failure` -- and on the next head
# ten sat in `action_required`. Both look identical to the merge gate, which
# reads check runs and reports "13 of 13 required status checks are expected".
checks_on_head() {
  gh api "repos/${REPO}/commits/${1}/check-runs" --jq '.total_count' 2>/dev/null || echo "unknown"
}

checks=$(checks_on_head "$head_sha")

if [ "$checks" = "0" ]; then
  echo "::warning::No check runs on ${head_sha}, so the release PR's required checks stay \"expected\" and the merge is refused. Re-firing them."
  bash "${refire}" "$branch"
  sleep "${CHECK_GRACE_S:-45}"
  checks=$(checks_on_head "$head_sha")
fi

# Said plainly, because the recovery has a ceiling: it reopens the PR with this
# job's credential, and a run whose actor is `github-actions[bot]` is what was
# producing no checks in the first place. When that is what happens, the
# release needs a human -- and a human who is told exactly what to run gets it
# done in a minute rather than an hour (#1184).
if [ "$checks" = "0" ]; then
  echo "::error::The release PR still has no check runs, so it cannot be merged. Approve its pending runs, or reopen it, with an account that can:"
  echo "::error::  gh run list -R ${REPO} --branch ${branch} --json databaseId,name,conclusion --jq '.[]|select(.conclusion==\"action_required\")|.databaseId' | xargs -I{} gh api -X POST repos/${REPO}/actions/runs/{}/approve"
elif [ "$checks" = "unknown" ]; then
  echo "::warning::Could not read the check runs for ${head_sha}; if the release PR shows no checks, close and reopen it."
else
  echo "${checks} check run(s) on ${head_sha}."
fi

echo "Assembled changelog for ${version} onto ${branch}."
