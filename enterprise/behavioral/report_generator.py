"""Behavioral report generator -- JSON and PDF export -- BSL 1.1 licensed.

Assembles per-provider behavioral reports from profiling data stores:
BaselineStore (network destinations), SchemaTracker (tool schemas), and
ResourceStore (CPU/memory/network I/O samples). Produces structured JSON
dicts or PDF bytes via fpdf2.

See enterprise/LICENSE.BSL for license terms.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fpdf import FPDF

logger = structlog.get_logger(__name__)


class BehavioralReportGenerator:
    """Assembles per-provider behavioral reports from profiling data stores.

    Reads from BaselineStore (network destinations), SchemaTracker (tool schemas),
    and ResourceStore (CPU/memory/network I/O samples). Produces structured JSON
    dicts or PDF bytes.

    Args:
        baseline_store: BaselineStore for network observation data.
        schema_tracker: Optional SchemaTracker for tool schema history.
        resource_store: Optional ResourceStore for CPU/memory/network samples.
    """

    def __init__(
        self,
        baseline_store: Any,
        schema_tracker: Any | None = None,
        resource_store: Any | None = None,
    ) -> None:
        self._baseline_store = baseline_store
        self._schema_tracker = schema_tracker
        self._resource_store = resource_store

    def generate_json(self, provider_id: str) -> dict[str, Any]:
        """Generate a structured JSON behavioral report for a provider.

        Assembles data from all configured stores into a single report dict
        with sections for network destinations, schema history, resource usage,
        and deviation events.

        Args:
            provider_id: Identifier of the provider to report on.

        Returns:
            Report dict with keys: provider_id, generated_at, behavioral_mode,
            network_destinations, schema_history, resource_usage, deviation_events.
        """
        generated_at = datetime.now(UTC).isoformat()
        behavioral_mode = str(self._baseline_store.get_mode(provider_id))

        # Network destinations from BaselineStore
        network_destinations = self._baseline_store.get_observations(provider_id)

        # Schema history from SchemaTracker (if available)
        schema_history: list[dict[str, Any]] = []
        if self._schema_tracker is not None:
            schema_history = self._schema_tracker.get_snapshot(provider_id)

        # Resource usage from ResourceStore (if available)
        resource_usage: dict[str, Any]
        if self._resource_store is not None:
            samples = self._resource_store.get_samples(provider_id, limit=50)
            baseline = self._resource_store.get_baseline(provider_id)
            resource_usage = {"samples": samples, "baseline": baseline}
        else:
            resource_usage = {"samples": [], "baseline": None}

        return {
            "provider_id": provider_id,
            "generated_at": generated_at,
            "behavioral_mode": behavioral_mode,
            "network_destinations": network_destinations,
            "schema_history": schema_history,
            "resource_usage": resource_usage,
            "deviation_events": [],
        }

    def generate_pdf(self, provider_id: str) -> bytes:
        """Generate a PDF behavioral report for a provider.

        Calls generate_json() to assemble report data, then renders it
        into a multi-section PDF document using fpdf2.

        Sections:
        1. Network Destinations -- table of observed network connections
        2. Tool Schema History -- table of schema snapshots
        3. Resource Usage -- baseline summary + recent samples table

        Args:
            provider_id: Identifier of the provider to report on.

        Returns:
            PDF document as bytes (starts with ``%PDF``).
        """
        report = self.generate_json(provider_id)

        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Title
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, f"Behavioral Report: {provider_id}", new_x="LMARGIN", new_y="NEXT")

        # Metadata line
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(128, 128, 128)
        generated_at = report["generated_at"][:19]
        pdf.cell(
            0,
            6,
            f"Generated: {generated_at}  |  Mode: {report['behavioral_mode']}",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

        # Section 1: Network Destinations
        self._render_network_section(pdf, report["network_destinations"])

        # Section 2: Tool Schema History
        self._render_schema_section(pdf, report["schema_history"])

        # Section 3: Resource Usage
        self._render_resource_section(pdf, report["resource_usage"])

        return pdf.output()

    def _render_network_section(self, pdf: FPDF, destinations: list[dict[str, Any]]) -> None:
        """Render the Network Destinations section into the PDF."""
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Network Destinations", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        if not destinations:
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(0, 6, "No network destinations observed.", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
            return

        col_widths = (50, 15, 20, 35, 35, 25)
        headers = ("Host", "Port", "Protocol", "First Seen", "Last Seen", "Count")

        with pdf.table(col_widths=col_widths) as table:
            # Header row
            header_row = table.row()
            pdf.set_font("Helvetica", "B", 9)
            for header in headers:
                header_row.cell(header)

            # Data rows
            pdf.set_font("Helvetica", "", 9)
            for dest in destinations:
                row = table.row()
                row.cell(str(dest.get("host", "")))
                row.cell(str(dest.get("port", "")))
                row.cell(str(dest.get("protocol", "")))
                row.cell(str(dest.get("first_seen", ""))[:19])
                row.cell(str(dest.get("last_seen", ""))[:19])
                row.cell(str(dest.get("observation_count", "")))

        pdf.ln(4)

    def _render_schema_section(self, pdf: FPDF, schema_history: list[dict[str, Any]]) -> None:
        """Render the Tool Schema History section into the PDF."""
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Tool Schema History", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        if not schema_history:
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(0, 6, "No schema data available.", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
            return

        col_widths = (45, 40, 45, 45)
        headers = ("Tool Name", "Schema Hash", "First Seen", "Last Seen")

        with pdf.table(col_widths=col_widths) as table:
            header_row = table.row()
            pdf.set_font("Helvetica", "B", 9)
            for header in headers:
                header_row.cell(header)

            pdf.set_font("Helvetica", "", 9)
            for entry in schema_history:
                row = table.row()
                row.cell(str(entry.get("tool_name", "")))
                schema_hash = str(entry.get("schema_hash", ""))
                row.cell(schema_hash[:12])
                row.cell(str(entry.get("first_seen", ""))[:19])
                row.cell(str(entry.get("last_seen", ""))[:19])

        pdf.ln(4)

    def _render_resource_section(self, pdf: FPDF, resource_usage: dict[str, Any]) -> None:
        """Render the Resource Usage section into the PDF."""
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Resource Usage (Recent)", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        samples = resource_usage.get("samples", [])
        baseline = resource_usage.get("baseline")

        if not samples and baseline is None:
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(0, 6, "No resource data available.", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
            return

        # Baseline summary line
        if baseline is not None:
            pdf.set_font("Helvetica", "", 10)
            cpu_mean = baseline.get("cpu_mean", 0)
            memory_mean = baseline.get("memory_mean", 0)
            memory_mb = memory_mean / 1048576
            pdf.cell(
                0,
                6,
                f"Baseline: CPU mean={cpu_mean:.1f}%, Memory mean={memory_mb:.1f}MB",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.ln(2)

        if not samples:
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(0, 6, "No recent samples.", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
            return

        # Show last 20 samples
        display_samples = samples[:20]

        col_widths = (40, 22, 30, 30, 30)
        headers = ("Sampled At", "CPU %", "Memory MB", "Net RX KB", "Net TX KB")

        with pdf.table(col_widths=col_widths) as table:
            header_row = table.row()
            pdf.set_font("Helvetica", "B", 9)
            for header in headers:
                header_row.cell(header)

            pdf.set_font("Helvetica", "", 9)
            for sample in display_samples:
                row = table.row()
                row.cell(str(sample.get("sampled_at", ""))[:19])
                row.cell(f"{sample.get('cpu_percent', 0):.1f}")
                memory_mb = sample.get("memory_bytes", 0) / 1048576
                row.cell(f"{memory_mb:.1f}")
                rx_kb = sample.get("network_rx_bytes", 0) / 1024
                row.cell(f"{rx_kb:.1f}")
                tx_kb = sample.get("network_tx_bytes", 0) / 1024
                row.cell(f"{tx_kb:.1f}")

        pdf.ln(4)
