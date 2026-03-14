"""Log value objects for the domain layer.

Contains:
- LogLine - a single captured line of provider stdout/stderr output
"""

import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class LogLine:
    """A single captured line from a provider's stdout or stderr stream.

    Attributes:
        provider_id: The provider that produced this log line.
        stream: Which stream the line came from -- ``"stdout"`` or ``"stderr"``.
        content: The raw text content of the line (trailing newline stripped).
        recorded_at: Unix timestamp (seconds since epoch) when the line was captured.
    """

    provider_id: str
    stream: Literal["stdout", "stderr"]
    content: str
    recorded_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Serialize to a plain dictionary.

        Returns:
            Dictionary with ``provider_id``, ``stream``, ``content``, and ``recorded_at`` keys.
        """
        return {
            "provider_id": self.provider_id,
            "stream": self.stream,
            "content": self.content,
            "recorded_at": self.recorded_at,
        }
