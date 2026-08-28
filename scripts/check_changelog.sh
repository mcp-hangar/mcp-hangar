#!/usr/bin/env bash
set -euo pipefail

# Requires a changelog FRAGMENT on every non-trivial PR. See GIT_FLOW.md.
#
# This used to require a diff in CHANGELOG.md itself, under `## [Unreleased]`.
# That made one anchor in one file the write target for every open PR at once,
# so any two of them conflicted and the second to merge got a hand-resolve.
# A fragment is a new file per PR, so there is nothing for git to merge.
# `scripts/build_changelog.py` folds them into CHANGELOG.md at release time.

# Inputs from environment
: "${BASE_SHA:?BASE_SHA must be set}"
: "${HEAD_SHA:?HEAD_SHA must be set}"
: "${PR_LABELS:=}"
: "${PR_TITLE:=}"

TRIGGERING_PATTERN='^(src/|pyproject\.toml$|packages/(operator|helm-charts|ui)/)'
FRAGMENT_PATTERN='^changelog\.d/[A-Za-z0-9._-]+\.(added|changed|deprecated|removed|fixed|security)\.md$'

changed_files=$(git diff --name-only "$BASE_SHA".."$HEAD_SHA")

# Checked before anything else, and on every PR rather than only the ones that
# owe an entry: the mistake this catches -- writing the entry into CHANGELOG.md
# the way the old convention said to -- is most likely on a PR that owes no
# entry at all, where the fragment requirement below never runs. A hand edit is
# still legitimate for a typo in an already-released section, so this warns.
if echo "$changed_files" | grep -qx "CHANGELOG.md"; then
  echo "::warning file=CHANGELOG.md::CHANGELOG.md is generated from changelog.d/ at release time. A new entry belongs in a fragment; only edits to already-released sections survive. See changelog.d/README.md."
fi

if ! echo "$changed_files" | grep -qE "$TRIGGERING_PATTERN"; then
  echo "No triggering files changed. Changelog fragment not required."
  exit 0
fi

if echo "$PR_LABELS" | grep -q "skip-changelog"; then
  echo "skip-changelog label present. Skipping check."
  exit 0
fi

# A dependency bump owes no fragment (changelog.d/README.md says so), but it
# edits `pyproject.toml`, which is a triggering path -- so every Python
# dependabot PR failed this gate and sat red until somebody hand-labelled it.
# The rule was documented and simply not implemented. Read from the title
# rather than the author, so a human doing the same bump is treated the same.
# A bump worth an entry (a pin that changes what callers may install) can still
# add a fragment; only the requirement is lifted, not the possibility.
if echo "$PR_TITLE" | grep -qE '^(chore|ci|build)\(deps(-dev)?\)'; then
  echo "Dependency bump. Changelog fragment not required."
  exit 0
fi

# Only files ADDED by this PR count. An edit to someone else's pending fragment
# is not this PR's changelog entry.
added_fragments=$(git diff --name-only --diff-filter=A "$BASE_SHA".."$HEAD_SHA" \
  | grep -E "$FRAGMENT_PATTERN" || true)

if [ -z "$added_fragments" ]; then
  echo "::error::No changelog fragment added. Create \`changelog.d/<id>-<slug>.<kind>.md\`"
  echo "::error::(kind: added|changed|deprecated|removed|fixed|security) or apply the \`skip-changelog\` label."
  echo "::error::See changelog.d/README.md. Do NOT edit CHANGELOG.md directly -- it is assembled at release time."
  exit 1
fi

# The fragment has to render, not merely exist: a malformed one fails the
# release assembly instead, which is the worst possible moment to find out.
# shellcheck disable=SC2086
python3 scripts/build_changelog.py check $added_fragments

echo "Changelog fragment found:"
# Splitting is the point: one line per fragment path.
# shellcheck disable=SC2086
printf '  %s\n' $added_fragments
exit 0
