"""How far the event log has been handed to handlers.

`publish_to_stream` appends events and then calls handlers. Between those two
steps the process can die, and until now that lost the delivery outright: the
events were durably in the store, no handler had seen them, and nothing ever
looked again. At-most-once, on the path we describe publicly as an audit trail.

The fix does not need an outbox table. `events` already carries
`global_position INTEGER PRIMARY KEY AUTOINCREMENT` and the store already reads
by it, so the log *is* the outbox and the dual-write problem an outbox table
exists to solve does not arise here. What was missing is one durable number:
how far along that log delivery has actually got.

The contract is at-least-once. A position is advanced only after handlers have
been called, so a crash re-delivers rather than skips, and **handlers must be
idempotent on `event_id`**.

## This is the standalone mark, and it is only that

One number meant two things, and the second one does not survive a second
writer:

- **How far delivery got in this process.** Still true, still what this is.
- **How far delivery got, full stop.** Never true with peers. A replica that
  publishes advances this mark past events *another* replica appended and had
  not yet delivered -- so the sweep that exists to recover them skips them
  instead. And in the other direction it re-delivers a peer's events to local
  handlers, which is a second SIEM export and a second cost record for work
  another replica already accounted for.

Keying it per instance does not rescue it, in either direction. The instance
identity is minted per process (`domain.events.producer`), deliberately, so a
row keyed by it is never found again after a restart and the sweep replays the
entire log. Key it by something stable per replica instead and a pod that is
replaced -- which is what a rolling update does -- leaves its backlog under an
identity that never comes back, with the rows accumulating one per rollout.

So in a cluster there is no mark of this kind. **Effects follow the instance
that produced the event** (#790, phase 0.4): a replica exports its own work and
nobody else's, which is exactly-once by construction and needs no cursor,
because a tool call happens on exactly one replica. A replica's *view* is a
different question with a different answer -- the log head plus a snapshot,
ephemeral, dying with the pod.

The residual exposure, stated rather than discovered: an event appended by a pod
that died before its handler ran is not exported. The window is microseconds --
delivery is inline immediately after the append -- and the event is still in the
log, so the gateway's own audit trail is complete either way.
"""

from abc import ABC, abstractmethod


class IDispatchCheckpoint(ABC):
    """The high-water mark of event delivery, durable across restarts."""

    @abstractmethod
    def read(self) -> int:
        """The last global position handed to handlers.

        Returns:
            The position, or 0 when nothing has been dispatched yet. Positions
            are exclusive lower bounds, matching `IEventStore.read_all`.
        """

    @abstractmethod
    def advance(self, position: int) -> None:
        """Record that delivery has reached `position`.

        Never moves backwards: a caller that has just delivered an older batch
        must not undo a newer one. Implementations keep the maximum.

        Args:
            position: Global position of the last event handed to handlers.
        """
