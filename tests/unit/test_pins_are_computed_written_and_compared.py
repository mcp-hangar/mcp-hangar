"""`mcp-hangar pin` computes, writes and compares digests (#1191).

`digest_enforcement` has defaulted to `block` since 2.0.0 and the mismatch path
already refuses a call. What nothing did was tell an operator what to pin: the
only caller of `compute_tool_digest` was the projection registry, so adopting
the feature meant reproducing an RFC 8785 canonicalization by hand.

The starting half of the command is exercised against a real subprocess in
`tests/integration/test_pin_writes_and_detects_drift.py`. What is pinned here is
the reasoning applied to what came back, because each rule is a decision that a
future edit could quietly reverse:

- an unpinned tool is not drift (`pins` is a subset by design);
- a tool that vanished is drift, not silence;
- a server that failed to answer never rewrites its own pins;
- merging touches `tool_projection.pins` and leaves the rest of the file alone.
"""

from pathlib import Path

import yaml

from mcp_hangar.server.cli.services.pinning import (
    Drift,
    Observation,
    configured_pins,
    find_drift,
    merge_pins,
    write_config_with_pins,
)

PINNED = "a" * 64
SERVED = "b" * 64


def config_with(pins: dict[str, str] | None, **projection_extra) -> dict:
    projection: dict = dict(projection_extra)
    if pins is not None:
        projection["pins"] = pins
    return {
        "mcp_servers": {
            "probe": {
                "mode": "subprocess",
                "command": ["true"],
                "tool_projection": projection,
            }
        },
        "tool_access": {"mode": "front_door"},
    }


class TestReadingWhatIsPinned:
    def test_reads_the_all_tenants_block(self):
        assert configured_pins(config_with({"add": PINNED})) == {"probe": {"add": PINNED}}

    def test_a_server_without_pins_is_absent(self):
        assert configured_pins(config_with(None)) == {}

    def test_per_tenant_pins_are_not_read(self):
        # This command writes and checks the all-tenants block -- the one that
        # holds every caller (#902). A per-tenant pin answers a different
        # question and is not this command's to overwrite.
        config = config_with(None, tenant_overrides={"finance": {"pins": {"add": PINNED}}})

        assert configured_pins(config) == {}


class TestDrift:
    def test_a_changed_schema_is_drift(self):
        drift = find_drift(config_with({"add": PINNED}), [Observation("probe", {"add": SERVED})])

        assert drift == [Drift("probe", "add", PINNED, SERVED)]

    def test_a_matching_schema_is_not(self):
        assert find_drift(config_with({"add": PINNED}), [Observation("probe", {"add": PINNED})]) == []

    def test_a_tool_that_is_no_longer_served_is_drift(self):
        # Silence is the failure this whole feature exists to end: a pinned tool
        # that disappeared must not read as "nothing to check".
        drift = find_drift(config_with({"add": PINNED}), [Observation("probe", {"subtract": SERVED})])

        assert drift == [Drift("probe", "add", PINNED, None)]

    def test_an_unpinned_tool_is_not_drift(self):
        # `pins` is a subset by design; reporting every unpinned tool would make
        # `--check` fail for every deployment that pins part of its surface.
        assert find_drift(config_with({"add": PINNED}), [Observation("probe", {"add": PINNED, "extra": SERVED})]) == []

    def test_a_server_that_did_not_answer_reports_no_drift(self):
        # Not "no drift" as a verdict: the command exits 2 for an unreachable
        # server, so this only keeps an unanswered question out of the diff.
        assert find_drift(config_with({"add": PINNED}), [Observation("probe", error="never came up")]) == []


class TestMerging:
    def test_observed_digests_replace_the_pins(self):
        merged = merge_pins(config_with({"add": PINNED}), [Observation("probe", {"add": SERVED})])

        assert merged["mcp_servers"]["probe"]["tool_projection"]["pins"] == {"add": SERVED}

    def test_the_rest_of_the_server_spec_survives(self):
        merged = merge_pins(
            config_with({"add": PINNED}, digest_enforcement="block"), [Observation("probe", {"add": SERVED})]
        )

        spec = merged["mcp_servers"]["probe"]
        assert spec["mode"] == "subprocess"
        assert spec["command"] == ["true"]
        assert spec["tool_projection"]["digest_enforcement"] == "block"
        assert merged["tool_access"] == {"mode": "front_door"}

    def test_a_server_that_failed_keeps_its_pins(self):
        # Replacing them with nothing would unpin a tool the operator pinned,
        # silently, because a server was slow to start.
        merged = merge_pins(config_with({"add": PINNED}), [Observation("probe", error="never came up")])

        assert merged["mcp_servers"]["probe"]["tool_projection"]["pins"] == {"add": PINNED}

    def test_the_input_document_is_not_mutated(self):
        config = config_with({"add": PINNED})

        merge_pins(config, [Observation("probe", {"add": SERVED})])

        assert config["mcp_servers"]["probe"]["tool_projection"]["pins"] == {"add": PINNED}


class TestWriting:
    def test_the_previous_file_is_kept_beside_the_new_one(self, tmp_path: Path):
        config_path = tmp_path / "config.yaml"
        original = yaml.safe_dump(config_with({"add": PINNED}))
        config_path.write_text(original)

        backup = write_config_with_pins(
            config_path, merge_pins(yaml.safe_load(original), [Observation("probe", {"add": SERVED})])
        )

        assert backup.read_text() == original
        assert yaml.safe_load(config_path.read_text())["mcp_servers"]["probe"]["tool_projection"]["pins"] == {
            "add": SERVED
        }
