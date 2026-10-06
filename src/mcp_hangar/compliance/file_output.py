"""The file every compliance exporter appends its lines to (#1701).

One place for the four exporters' file branch. Each used to catch the
``OSError``, log it per record and drop the record, so a missing directory
meant an operator who configured SIEM export ran with none and a log full of
the same error. A failed write is now counted
(``mcp_hangar_compliance_export_failures_total``), shown on the health surfaces
through :class:`ComplianceExportStatus`, and logged once per burst: on the first
failure, and on the write that recovers, with the number dropped between.

:func:`probe` is the check bootstrap runs before it registers the feed, so a
path that cannot be appended to refuses startup instead.
"""

from __future__ import annotations

import errno
import threading
from pathlib import Path

from mcp_hangar.logging_config import get_logger
from mcp_hangar.metrics import record_compliance_export_failure
from mcp_hangar.observability.health import ComplianceExportStatus

logger = get_logger(__name__)


def failure_reason(error: OSError) -> str:
    """The bounded ``reason`` label for a failed write."""
    if isinstance(error, FileNotFoundError):
        return "not_found"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, IsADirectoryError):
        return "is_a_directory"
    if error.errno in (errno.ENOSPC, errno.EDQUOT):
        return "no_space"
    return "os_error"


def probe(output_path: str | Path) -> None:
    """Open *output_path* for append and close it; raises the ``OSError`` a write would.

    Opening is the check, not ``os.access``: that answers for the real uid and
    says yes to root on a read-only mount. The file is created if absent, as
    the first record would create it.
    """
    with Path(output_path).open("a", encoding="utf-8"):
        pass


class FileOutput:
    """Appends lines to one file, and keeps the feed's write health."""

    def __init__(self, output_path: Path, format_name: str) -> None:
        self._path = output_path
        self._lock = threading.Lock()
        self._dropped_in_burst = 0
        self.status = ComplianceExportStatus(format=format_name, output=str(output_path))

    def append(self, line: str) -> bool:
        """Append *line*; False when the write failed and the record was dropped."""
        try:
            with self._path.open("a", encoding="utf-8") as f:
                _ = f.write(line + "\n")
        except OSError as e:
            reason = failure_reason(e)
            record_compliance_export_failure(self.status.format, reason)
            with self._lock:
                first = not self.status.failing
                self.status.failing = True
                self.status.failures += 1
                self.status.last_reason = reason
                self._dropped_in_burst += 1
            if first:
                logger.error(
                    "compliance_export_write_failed",
                    format=self.status.format,
                    output=str(self._path),
                    reason=reason,
                    error=str(e),
                )
            return False
        if self.status.failing:
            with self._lock:
                recovered, dropped = self.status.failing, self._dropped_in_burst
                self.status.failing = False
                self._dropped_in_burst = 0
            if recovered:
                logger.warning(
                    "compliance_export_write_recovered",
                    format=self.status.format,
                    output=str(self._path),
                    dropped=dropped,
                )
        return True
