"""Unit tests for behavioral report REST endpoint -- 403 gate, JSON, PDF, 400.

Covers:
- Endpoint returns 403 JSON when report_generator is None (enterprise not loaded)
- Endpoint returns 200 JSON with correct structure for format=json
- Endpoint returns 200 PDF for format=pdf with application/pdf content type
- Endpoint returns 200 JSON as default when no format query param
- Endpoint returns 400 for unsupported format (xml)

Uses Starlette TestClient with monkeypatched _get_report_generator.
"""

from unittest.mock import patch

from starlette.applications import Starlette
from starlette.testclient import TestClient

from enterprise.behavioral.api.reports import behavioral_report_routes
from enterprise.behavioral.baseline_store import BaselineStore
from enterprise.behavioral.report_generator import BehavioralReportGenerator
from enterprise.behavioral.resource_store import ResourceStore
from mcp_hangar.domain.value_objects.behavioral import NetworkObservation, ResourceSample


def _build_client(report_generator):
    """Build a Starlette TestClient with a patched report generator."""
    app = Starlette(routes=behavioral_report_routes)
    client = TestClient(app)
    return client, report_generator


def _make_generator() -> BehavioralReportGenerator:
    """Create a real BehavioralReportGenerator with seeded in-memory stores."""
    bs = BaselineStore(":memory:")
    rs = ResourceStore(":memory:")

    # Seed some data
    obs = NetworkObservation(1.0, "math", "api.example.com", 443, "tcp", "outbound")
    bs.record_observation(obs)
    sample = ResourceSample("math", "2026-03-25T12:00:00", 15.5, 512_000, 1_024_000, 200, 300)
    rs.record_sample(sample)

    return BehavioralReportGenerator(bs, resource_store=rs)


class TestBehavioralReportEndpoint:
    """Tests for GET /{provider_id}/behavioral-report endpoint."""

    def test_endpoint_403_when_no_report_generator(self) -> None:
        """Returns 403 JSON when report_generator is None."""
        app = Starlette(routes=behavioral_report_routes)
        client = TestClient(app)

        with patch("enterprise.behavioral.api.reports._get_report_generator", return_value=None):
            resp = client.get("/math/behavioral-report")

        assert resp.status_code == 403
        body = resp.json()
        assert "error" in body

    def test_endpoint_json_report(self) -> None:
        """Returns 200 JSON with correct structure for format=json."""
        gen = _make_generator()
        app = Starlette(routes=behavioral_report_routes)
        client = TestClient(app)

        with patch("enterprise.behavioral.api.reports._get_report_generator", return_value=gen):
            resp = client.get("/math/behavioral-report?format=json")

        assert resp.status_code == 200
        body = resp.json()
        assert body["provider_id"] == "math"
        assert "network_destinations" in body
        assert "schema_history" in body
        assert "resource_usage" in body

    def test_endpoint_pdf_report(self) -> None:
        """Returns 200 PDF with application/pdf content type."""
        gen = _make_generator()
        app = Starlette(routes=behavioral_report_routes)
        client = TestClient(app)

        with patch("enterprise.behavioral.api.reports._get_report_generator", return_value=gen):
            resp = client.get("/math/behavioral-report?format=pdf")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content[:4] == b"%PDF"

    def test_endpoint_default_format_json(self) -> None:
        """Returns 200 JSON when no format query param provided."""
        gen = _make_generator()
        app = Starlette(routes=behavioral_report_routes)
        client = TestClient(app)

        with patch("enterprise.behavioral.api.reports._get_report_generator", return_value=gen):
            resp = client.get("/math/behavioral-report")

        assert resp.status_code == 200
        body = resp.json()
        assert body["provider_id"] == "math"

    def test_endpoint_400_unsupported_format(self) -> None:
        """Returns 400 for format=xml (unsupported)."""
        gen = _make_generator()
        app = Starlette(routes=behavioral_report_routes)
        client = TestClient(app)

        with patch("enterprise.behavioral.api.reports._get_report_generator", return_value=gen):
            resp = client.get("/math/behavioral-report?format=xml")

        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body
        assert "xml" in body["error"].lower() or "Unsupported" in body["error"]
