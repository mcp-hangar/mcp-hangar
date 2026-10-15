"""Every image under `examples/` is either pulled in CI or declared a placeholder (#1681).

The resolution itself needs a registry and runs in `.github/workflows/example-images.yml`.
This is the part that does not: the scan finds the images, the dead names are
gone, and every declared placeholder is still in use (a stale exemption is a hole).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_example_images", ROOT / "scripts" / "check_example_images.py")
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

DEAD = (
    "ghcr.io/modelcontextprotocol/mcp-sqlite",
    "ghcr.io/modelcontextprotocol/mcp-github",
    "ghcr.io/modelcontextprotocol/mcp-postgres",
)


def test_the_scan_finds_the_examples_images():
    images = checker.referenced_images(ROOT)
    assert len(images) >= 10, sorted(images)
    assert "ghcr.io/mcp-hangar/mcp-hangar:latest" in images


def test_the_images_that_cannot_be_pulled_are_not_referenced():
    referenced = checker.referenced_images(ROOT)
    for dead in DEAD:
        assert not [i for i in referenced if i.startswith(dead)], dead


def test_every_declared_placeholder_is_still_in_an_example():
    referenced = checker.referenced_images(ROOT)
    assert set(checker.NOT_PULLED) <= set(referenced), set(checker.NOT_PULLED) - set(referenced)


def test_variables_and_local_images_are_not_asked_for(tmp_path):
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "a.yaml").write_text(
        "services:\n"
        "  a:\n    image: ${IMG:?set it}\n"
        "  b:\n    image: localhost/built:test\n"
        "  c:\n    image: busybox:1\n"
    )
    assert checker.referenced_images(tmp_path) == {"busybox:1": ["examples/a.yaml"]}
