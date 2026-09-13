#!/usr/bin/env bash
set -euo pipefail

# Push HEAD onto the release branch as the identity PUSH_TOKEN belongs to.
#
# A token in the remote URL is not enough on its own. `actions/checkout`
# persists the job's GITHUB_TOKEN as an `http.<server>/.extraheader`
# (`AUTHORIZATION: basic ...`), and git sends that header on every request to
# the server. GitHub accepts it, so the request never reaches the 401 that
# would make git offer the URL's credentials. The push therefore succeeded as
# `github-actions[bot]`, and GitHub does not start workflows for a
# GITHUB_TOKEN push. That was every assembly push to the release branch on
# 2026-09-11/12. The Activity API shows `github-actions[bot]` as the pusher
# of 8bd2fc00..7acb7bb9 (2.19.0) and a8ba482f..d6ccc281 (2.19.1). The
# commits were signed as the release app, and the app token was in the URL
# (#1379).
#
# An empty value resets the extra-header list for this URL (git-config
# `http.extraHeader`), and `-c` is read after every file, including the
# `includeIf` file that checkout v6+ writes. Only the URL's credential is left.
#
# Its own file so the credential that actually reaches the server can be tested
# without a release: `tests/ci/test_the_release_push_is_made_as_the_app.py`
# runs it against a local HTTP server that records the Authorization header.
#
# Usage: push_release_branch.sh <branch>
# Inputs: PUSH_TOKEN (the credential to push as), GITHUB_REPOSITORY (defaults
# to the core repo), GITHUB_SERVER_URL (defaults to https://github.com).

branch="${1:?branch is required}"
: "${PUSH_TOKEN:?PUSH_TOKEN must be set}"
repo="${GITHUB_REPOSITORY:-mcp-hangar/mcp-hangar}"
server="${GITHUB_SERVER_URL:-https://github.com}"
server="${server%/}"
scheme="${server%%://*}"
host="${server#*://}"

git -c "http.${server}/.extraheader=" \
  push "${scheme}://x-access-token:${PUSH_TOKEN}@${host}/${repo}.git" "HEAD:${branch}"
