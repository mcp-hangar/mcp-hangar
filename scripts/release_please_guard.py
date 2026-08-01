#!/usr/bin/env python3
"""Two guards for the release-please run, both about the same blind spot.

`release-please.yml` fires on push to `main`. A hand-cut release merges its
release commit first and pushes the tag second, so a run landing in that window
finds no tag matching the version the manifest now carries, falls back to an
older baseline, and derives a version from the whole commit range. Twice in one
day it proposed moving `.release-please-manifest.json` *backwards* -- #670 from
2.0.0 to 1.6.1, #677 from 2.0.1 to 1.6.1 -- while rewriting CHANGELOG.md with
1.x history under a `v2.0.0...v1.6.1` compare link. Nothing is wrong with
release-please: the tag is simply not there yet, and its fallback assumes the
manifest is the stale thing. That assumption holds for a bot-driven release and
inverts for a hand-cut one.

Both occurrences were caught by a human reading the diff, which is not a control.

`should-run` removes the noise. A push whose release commit names the version
the manifest already carries has nothing left to propose, so the run is skipped.
It deliberately does *not* skip a release-please-authored release PR landing:
that run is the one that creates the tag and the GitHub release (v1.6.0 through
v1.6.3 were all cut that way), and skipping it would break bot releases outright.
The two are told apart by where the commit came from, which is the only thing
that differs -- both land a `chore(release): release X` commit, and in both the
tag is missing at the moment release-please looks.

`check-bump` catches the class rather than the instance. Whatever version
release-please computed -- released or merely proposed in a PR -- if it is lower
than the manifest's, the run fails loudly instead of leaving a wrong PR standing
quietly. It costs nothing on a healthy run and does not care *why* the baseline
was wrong, so it also covers the next cause, whatever that turns out to be.

Usage:
    HEAD_SUBJECT=... PR_TITLE=... PR_BRANCH=... python scripts/release_please_guard.py should-run
    RELEASE_PLEASE_OUTPUTS='{...}' python scripts/release_please_guard.py check-bump
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from packaging.version import InvalidVersion, Version

MANIFEST_PATH = Path(".release-please-manifest.json")
MANIFEST_KEY = "."

# `chore(release): release 2.0.1 (#676)` -- the `(#N)` tail is what a squash
# merge appends, and it can appear twice on a re-landed release
# (`chore(release): release 1.6.3 (#620) (#653)`). A PR title carries no tail.
RELEASE_SUBJECT = re.compile(r"^chore\(release\): release (?P<version>\S+?)(?:\s+\(#\d+\))*$")

# release-please raises its own release PR from this branch. A push whose commit
# came from it is a bot release landing, and that run must go ahead -- it is the
# step that creates the tag.
BOT_BRANCH_PREFIX = "release-please--"


def manifest_version() -> str:
    return str(json.loads(MANIFEST_PATH.read_text())[MANIFEST_KEY])


def release_version_of(subject: str) -> str | None:
    """Pull the version out of a `chore(release): release X` subject or PR title."""
    match = RELEASE_SUBJECT.match(subject.strip())
    return match.group("version") if match else None


def compare(left: str, right: str) -> str:
    """Return 'lt', 'eq' or 'gt' for two PEP 440 versions.

    These are PEP 440, not semver, and the orderings that matter are exactly the
    ones a string comparison gets wrong: '2.0.1' sorts *below* '1.6.1' lexically
    once the minor reaches double digits, and '2.0.0rc4' sorts *above* '2.0.0'.
    Getting either backwards means waving a backwards bump through or blocking
    every legitimate release.
    """
    a, b = Version(left), Version(right)
    if a < b:
        return "lt"
    if a > b:
        return "gt"
    return "eq"


def emit(**outputs: str) -> None:
    """Write step outputs, when running under Actions."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def decide(*, head_subject: str, pr_title: str, pr_branch: str, event_name: str, manifest: str) -> tuple[bool, str]:
    """Return (skip, reason) for this push."""
    if event_name == "workflow_dispatch":
        return False, "manual dispatch is the deliberate override, so it is never skipped"

    if pr_branch.startswith(BOT_BRANCH_PREFIX):
        return False, (
            f"the commit came from {pr_branch!r} -- this is release-please's own release PR landing, "
            "and this run is what creates the tag"
        )

    # A merge commit's subject names the branch, not the version; the PR title
    # carries it. A squash merge is the other way round. Accept either.
    version = release_version_of(head_subject) or release_version_of(pr_title)
    if version is None:
        return False, "HEAD is not a release commit"

    try:
        same = compare(version, manifest) == "eq"
    except InvalidVersion:
        same = version == manifest
    if not same:
        return False, f"HEAD releases {version}, which the manifest ({manifest}) does not already carry"

    return True, (
        f"HEAD is the hand-cut release of {version}, which .release-please-manifest.json already carries. "
        "Its tag is not pushed yet, so release-please would fall back to a stale baseline and propose a "
        "backwards bump (#681). There is nothing left to release here."
    )


def collect_computed_versions(outputs: dict[str, object]) -> list[str]:
    """Every version release-please arrived at, released or merely proposed.

    A release sets `version`; a proposal only exists inside the PR it opened, and
    a proposal is precisely the shape both incidents took.
    """
    found: list[str] = []

    for key, value in outputs.items():
        if key.lower().endswith("version") and isinstance(value, str) and value:
            found.append(value)

    for key in ("pr", "prs"):
        raw = outputs.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        entries = parsed if isinstance(parsed, list) else [parsed]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            version = release_version_of(str(entry.get("title", "")))
            if version:
                found.append(version)

    return list(dict.fromkeys(found))


def cmd_should_run() -> int:
    manifest = manifest_version()
    skip, reason = decide(
        head_subject=os.environ.get("HEAD_SUBJECT", ""),
        pr_title=os.environ.get("PR_TITLE", ""),
        pr_branch=os.environ.get("PR_BRANCH", ""),
        event_name=os.environ.get("GITHUB_EVENT_NAME", ""),
        manifest=manifest,
    )
    print(f"{'SKIP' if skip else 'RUN'}: {reason}")
    emit(skip=str(skip).lower(), manifest=manifest)
    return 0


def cmd_check_bump() -> int:
    manifest = manifest_version()
    raw = os.environ.get("RELEASE_PLEASE_OUTPUTS", "").strip() or "{}"
    try:
        outputs = json.loads(raw)
    except ValueError:
        print(f"::warning::release-please outputs are not JSON; the backwards-bump guard could not run: {raw[:200]}")
        return 0
    if not isinstance(outputs, dict):
        outputs = {}

    computed = collect_computed_versions(outputs)
    if not computed:
        print(f"No version computed; manifest stays at {manifest}.")
        return 0

    backwards = []
    for version in computed:
        try:
            relation = compare(version, manifest)
        except InvalidVersion:
            print(f"::warning::{version!r} is not a PEP 440 version; not compared against {manifest}")
            continue
        print(f"release-please computed {version}; manifest carries {manifest} ({relation})")
        if relation == "lt":
            backwards.append(version)

    if backwards:
        listed = ", ".join(backwards)
        print(
            f"::error title=release-please proposed a backwards version bump::"
            f"computed {listed}, but .release-please-manifest.json carries {manifest}"
        )
        print(
            f"\nFAIL: release-please computed {listed} against a manifest already at {manifest}.\n"
            f"  A release never moves backwards, so its baseline is wrong -- almost certainly a hand-cut\n"
            f"  release whose tag is not pushed yet (#681). Do NOT merge the pull request this run opened:\n"
            f"  it lowers .release-please-manifest.json and rewrites CHANGELOG.md with older history.\n"
            f"  Close it, push the missing tag, and let the next push to main re-derive the version.",
            file=sys.stderr,
        )
        return 1

    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("should-run", "check-bump"))
    args = parser.parse_args(argv)
    return cmd_should_run() if args.command == "should-run" else cmd_check_bump()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
