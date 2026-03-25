"""Unit tests for enterprise BehavioralReportGenerator -- JSON structure, PDF, optional stores.

Covers:
- generate_json returns dict with all required top-level keys
- generate_json for empty provider returns valid dict with empty lists
- generate_json includes network destinations from BaselineStore
- generate_json includes schema history from SchemaTracker
- generate_json includes resource usage from ResourceStore
- generate_json without optional stores returns empty sections
- generate_pdf returns bytes starting with %PDF
- generate_pdf with data produces non-trivial PDF
- generate_pdf for empty provider produces valid PDF

All tests use real in-memory SQLite stores -- no mocking.
"""

from enterprise.behavioral.baseline_store import BaselineStore
from enterprise.behavioral.report_generator import BehavioralReportGenerator
from enterprise.behavioral.resource_store import ResourceStore
from enterprise.behavioral.schema_tracker import SchemaTracker
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.value_objects.behavioral import NetworkObservation, ResourceSample

_REQUIRED_JSON_KEYS = {
    "provider_id",
    "generated_at",
    "behavioral_mode",
    "network_destinations",
    "schema_history",
    "resource_usage",
    "deviation_events",
}


def _make_observation(provider_id: str = "math", host: str = "api.example.com") -> NetworkObservation:
    """Factory for NetworkObservation with sensible defaults."""
    return NetworkObservation(
        timestamp=1.0,
        provider_id=provider_id,
        destination_host=host,
        destination_port=443,
        protocol="tcp",
        direction="outbound",
    )


def _make_sample(
    provider_id: str = "math",
    sampled_at: str = "2026-03-25T12:00:00",
) -> ResourceSample:
    """Factory for ResourceSample with sensible defaults."""
    return ResourceSample(
        provider_id=provider_id,
        sampled_at=sampled_at,
        cpu_percent=15.5,
        memory_bytes=512_000,
        memory_limit_bytes=1_024_000,
        network_rx_bytes=200,
        network_tx_bytes=300,
    )


def _make_tool(name: str = "add", input_schema: dict | None = None) -> ToolSchema:
    """Factory for ToolSchema with sensible defaults."""
    return ToolSchema(
        name=name,
        description=f"Tool {name}",
        input_schema=input_schema or {"type": "object", "properties": {"x": {"type": "integer"}}},
    )


class TestReportGeneratorJSON:
    """Tests for generate_json output structure and content."""

    def test_json_report_structure(self) -> None:
        """generate_json returns dict with all required top-level keys."""
        bs = BaselineStore(":memory:")
        gen = BehavioralReportGenerator(bs)
        report = gen.generate_json("math")

        assert isinstance(report, dict)
        assert _REQUIRED_JSON_KEYS.issubset(report.keys())
        assert report["provider_id"] == "math"

    def test_json_report_empty_provider(self) -> None:
        """generate_json for provider with no data returns valid dict with empty lists."""
        bs = BaselineStore(":memory:")
        gen = BehavioralReportGenerator(bs)
        report = gen.generate_json("nonexistent")

        assert report["provider_id"] == "nonexistent"
        assert report["network_destinations"] == []
        assert report["schema_history"] == []
        assert report["resource_usage"]["samples"] == []
        assert report["resource_usage"]["baseline"] is None

    def test_json_report_includes_network_destinations(self) -> None:
        """Seed BaselineStore, verify report contains the destinations."""
        bs = BaselineStore(":memory:")
        bs.record_observation(_make_observation("math", "api.example.com"))
        bs.record_observation(_make_observation("math", "cdn.other.com"))

        gen = BehavioralReportGenerator(bs)
        report = gen.generate_json("math")

        destinations = report["network_destinations"]
        assert len(destinations) == 2
        hosts = {d["host"] for d in destinations}
        assert "api.example.com" in hosts
        assert "cdn.other.com" in hosts

    def test_json_report_includes_schema_history(self) -> None:
        """Seed SchemaTracker with check_and_store call, verify schema entries."""
        bs = BaselineStore(":memory:")
        st = SchemaTracker(":memory:")

        # Store an initial schema snapshot
        st.check_and_store("math", [_make_tool("add"), _make_tool("subtract")])

        gen = BehavioralReportGenerator(bs, schema_tracker=st)
        report = gen.generate_json("math")

        assert len(report["schema_history"]) == 2
        tool_names = {entry["tool_name"] for entry in report["schema_history"]}
        assert tool_names == {"add", "subtract"}

    def test_json_report_includes_resource_usage(self) -> None:
        """Seed ResourceStore with samples + baseline, verify resource_usage."""
        bs = BaselineStore(":memory:")
        rs = ResourceStore(":memory:")

        # Record enough samples for baseline computation
        for i in range(12):
            rs.record_sample(_make_sample(sampled_at=f"2026-03-25T{10 + i:02d}:00:00"))
        rs.compute_and_store_baseline("math")

        gen = BehavioralReportGenerator(bs, resource_store=rs)
        report = gen.generate_json("math")

        resource = report["resource_usage"]
        assert len(resource["samples"]) == 12
        assert resource["baseline"] is not None
        assert "cpu_mean" in resource["baseline"]

    def test_json_report_without_optional_stores(self) -> None:
        """Generator with only BaselineStore has empty schema and resource sections."""
        bs = BaselineStore(":memory:")
        gen = BehavioralReportGenerator(bs)
        report = gen.generate_json("math")

        assert report["schema_history"] == []
        assert report["resource_usage"]["samples"] == []
        assert report["resource_usage"]["baseline"] is None


class TestReportGeneratorPDF:
    """Tests for generate_pdf output."""

    def test_pdf_report_valid(self) -> None:
        """generate_pdf returns bytes starting with %PDF."""
        bs = BaselineStore(":memory:")
        gen = BehavioralReportGenerator(bs)
        pdf = gen.generate_pdf("math")

        assert isinstance(pdf, bytes)
        assert pdf[:4] == b"%PDF"

    def test_pdf_report_nonempty(self) -> None:
        """generate_pdf with data produces PDF larger than minimal."""
        bs = BaselineStore(":memory:")
        bs.record_observation(_make_observation("math", "api.example.com"))

        rs = ResourceStore(":memory:")
        rs.record_sample(_make_sample())

        gen = BehavioralReportGenerator(bs, resource_store=rs)
        pdf = gen.generate_pdf("math")

        # Should be non-trivially sized with actual data tables
        assert len(pdf) > 200

    def test_pdf_report_empty_provider(self) -> None:
        """generate_pdf for empty provider still produces valid PDF."""
        bs = BaselineStore(":memory:")
        gen = BehavioralReportGenerator(bs)
        pdf = gen.generate_pdf("nonexistent")

        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 100
