"""A GitHub Release carries the artifacts and their provenance.

Every tag already produced a Release, and the wheel was already attested at
build time -- but the Release carried no assets, so the attestation existed
only inside the workflow run and OpenSSF Signed-Releases reported "no releases
found" for a repository with 20 of them. An attestation nobody can fetch beside
the artifact is not provenance.
"""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
RELEASE = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())


def _steps(job: str) -> list[dict]:
    return RELEASE["jobs"][job]["steps"]


def test_the_wheel_is_attested_before_it_is_published() -> None:
    names = [step.get("uses", "") for step in _steps("publish-pypi")]

    assert any("attest-build-provenance" in use for use in names)


def test_the_artifacts_reach_the_release_job() -> None:
    """`publish-pypi` builds them; `create-release` is a different runner."""
    uploads = [step for step in _steps("publish-pypi") if "upload-artifact" in step.get("uses", "")]
    downloads = [step for step in _steps("create-release") if "download-artifact" in step.get("uses", "")]

    assert uploads and downloads
    assert uploads[0]["with"]["name"] == downloads[0]["with"]["name"]


def test_the_release_attaches_the_artifacts_and_the_provenance() -> None:
    release_step = next(step for step in _steps("create-release") if "action-gh-release" in step.get("uses", ""))
    attached = release_step["with"]["files"]

    assert ".whl" in attached
    assert ".tar.gz" in attached
    # The bundle is what makes the release verifiable, and what Signed-Releases
    # reads. Losing it would leave the check unscored with the assets in place.
    assert ".intoto.jsonl" in attached
