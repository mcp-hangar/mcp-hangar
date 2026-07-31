"""Tests for W3C baggage propagation and the fail-safe cross-tenant scrub (SEP-414).

Interpretation B: propagate W3C ``baggage`` alongside trace context, but strip it
across a tenant boundary. Baggage keys/values are opaque, so the scrub is
conservative by default: only Hangar-owned baggage attributable to the current
tenant survives on the outbound path; inbound-originated / cross-tenant baggage
is dropped.
"""

from unittest.mock import patch

import pytest

from mcp_hangar.observability.tracing import (
    HANGAR_BAGGAGE_TENANT_KEY,
    extract_trace_context,
    inject_trace_context,
    scrub_baggage_for_tenant,
)


class TestBaggageRoundTrip:
    """Baggage must be extracted from inbound carriers and available in-context."""

    def test_inbound_baggage_extracted_and_available(self) -> None:
        """Baggage present on an inbound carrier round-trips through extract/inject."""
        # extract_trace_context is a no-op unless the full OTEL SDK is installed
        # (OTEL_AVAILABLE). Gate on the SDK, not just opentelemetry.baggage: mcp v2
        # pulls opentelemetry-api transitively, so an api-only (SDK-less) dev env
        # imports opentelemetry.baggage yet extract_trace_context still returns None.
        pytest.importorskip("opentelemetry.sdk.trace")

        from opentelemetry import baggage
        from opentelemetry import context as otel_context

        inbound = {"baggage": "hangar.tenant=t1,user.id=alice"}

        with patch("mcp_hangar.observability.tracing._initialized", True):
            ctx = extract_trace_context(inbound)

        # Extracted baggage is available in the returned context.
        extracted = baggage.get_all(ctx)
        assert extracted.get("hangar.tenant") == "t1"
        assert extracted.get("user.id") == "alice"

        # inject reads the *current* OTel context, so attach the extracted one.
        token = otel_context.attach(ctx)
        try:
            outbound: dict[str, str] = {}
            with patch("mcp_hangar.observability.tracing._initialized", True):
                inject_trace_context(outbound)
        finally:
            otel_context.detach(token)

        assert "baggage" in outbound
        assert "hangar.tenant=t1" in outbound["baggage"]
        assert "user.id=alice" in outbound["baggage"]


class TestCrossTenantScrub:
    """scrub_baggage_for_tenant drops untrusted/cross-tenant baggage on outbound."""

    def test_inbound_originated_baggage_is_dropped(self) -> None:
        """Non-Hangar (inbound-originated / third-party) baggage is stripped."""
        carrier = {"baggage": "user.id=alice,session=abc,other.tenant=t2"}
        scrub_baggage_for_tenant(carrier, current_tenant_id="t1")
        # Nothing was Hangar-owned, so the whole baggage entry is removed.
        assert "baggage" not in carrier

    def test_hangar_baggage_for_same_tenant_is_preserved(self) -> None:
        """Hangar-set baggage attributable to the current tenant survives."""
        carrier = {"baggage": f"{HANGAR_BAGGAGE_TENANT_KEY}=t1,hangar.feature=x"}
        scrub_baggage_for_tenant(carrier, current_tenant_id="t1")
        assert carrier["baggage"] == f"{HANGAR_BAGGAGE_TENANT_KEY}=t1,hangar.feature=x"

    def test_mixed_baggage_keeps_only_hangar_owned(self) -> None:
        """Only the Hangar-owned entries are kept when mixed with untrusted ones."""
        carrier = {"baggage": "hangar.feature=x,user.id=alice"}
        scrub_baggage_for_tenant(carrier, current_tenant_id="t1")
        assert carrier["baggage"] == "hangar.feature=x"

    def test_cross_tenant_marker_drops_all_baggage(self) -> None:
        """A Hangar tenant marker for a different tenant drops the whole carrier."""
        carrier = {"baggage": f"{HANGAR_BAGGAGE_TENANT_KEY}=t2,hangar.feature=x"}
        scrub_baggage_for_tenant(carrier, current_tenant_id="t1")
        assert "baggage" not in carrier

    def test_unknown_tenant_drops_tenant_marked_baggage(self) -> None:
        """When the current tenant is unknown, tenant-marked baggage is not forwarded."""
        carrier = {"baggage": f"{HANGAR_BAGGAGE_TENANT_KEY}=t1"}
        scrub_baggage_for_tenant(carrier, current_tenant_id=None)
        assert "baggage" not in carrier

    def test_no_baggage_header_is_a_noop(self) -> None:
        """A carrier with no baggage entry is untouched."""
        carrier = {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
        scrub_baggage_for_tenant(carrier, current_tenant_id="t1")
        assert carrier == {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}

    def test_scrub_works_without_otel_installed(self) -> None:
        """The scrub is a pure-string boundary and does not require OTEL."""
        with patch("mcp_hangar.observability.tracing.OTEL_AVAILABLE", False):
            carrier = {"baggage": "user.id=alice"}
            scrub_baggage_for_tenant(carrier, current_tenant_id="t1")
            assert "baggage" not in carrier


class TestTraceContextUnchanged:
    """Adding baggage handling must not drop traceparent/tracestate propagation."""

    def test_traceparent_still_propagates(self) -> None:
        """A valid inbound traceparent still round-trips through inject/extract."""
        pytest.importorskip("opentelemetry.sdk.trace")

        from opentelemetry import context as otel_context

        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        inbound = {"traceparent": traceparent, "baggage": "user.id=alice"}

        with patch("mcp_hangar.observability.tracing._initialized", True):
            ctx = extract_trace_context(inbound)

        token = otel_context.attach(ctx)
        try:
            outbound: dict[str, str] = {}
            with patch("mcp_hangar.observability.tracing._initialized", True):
                inject_trace_context(outbound)
        finally:
            otel_context.detach(token)

        # Trace context is preserved: same trace id in the outbound traceparent.
        assert "traceparent" in outbound
        assert "4bf92f3577b34da6a3ce929d0e0e4736" in outbound["traceparent"]

    def test_scrub_does_not_touch_traceparent(self) -> None:
        """scrub only removes baggage; traceparent/tracestate are left intact."""
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        carrier = {"traceparent": traceparent, "baggage": "user.id=alice"}
        scrub_baggage_for_tenant(carrier, current_tenant_id="t1")
        assert carrier["traceparent"] == traceparent
        assert "baggage" not in carrier
