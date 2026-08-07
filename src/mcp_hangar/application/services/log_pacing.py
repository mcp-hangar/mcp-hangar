"""Saying a repeated failure once, then occasionally, then once when it ends.

Two background loops poll something that can be down for a long time: the lease
keeper and the event tailer. Both got it wrong, in opposite directions, and both
were found by watching a real deployment rather than a test.

The tailer logged **every** failed read. A wedged database produced thirty lines
a minute per replica, which is the volume at which an operator stops reading.

The keeper logged its failed acquisitions at `debug`, which in a production log
level is silence. An instance that could not reach the store never became the
manager and never said so: the fleet simply stopped converging, and the only
clue anywhere was the tailer's separate complaint about a different table.

So: the first failure is worth a warning, the hundredth is not, and the recovery
is worth one line -- because "it started working again" is the fact an operator
most often has to establish from absence.
"""

from __future__ import annotations

#: Report the first failure, then roughly once a minute at a two-second poll.
DEFAULT_EVERY = 30


class RepeatedFailure:
    """Counts a run of failures and says when it is worth reporting."""

    def __init__(self, every: int = DEFAULT_EVERY) -> None:
        """
        Args:
            every: After the first, report one in this many.
        """
        self._every = max(1, every)
        self._run = 0

    @property
    def run_length(self) -> int:
        """How many failures in a row, including the one just recorded."""
        return self._run

    def failed(self) -> bool:
        """Record a failure. Returns whether this one is worth reporting."""
        self._run += 1
        return self._run == 1 or self._run % self._every == 0

    def recovered(self) -> bool:
        """Record a success. Returns whether the recovery is worth reporting.

        True only when something had actually been failing, so a loop that has
        been healthy all along stays quiet.
        """
        had_failed = self._run > 0
        self._run = 0
        return had_failed
