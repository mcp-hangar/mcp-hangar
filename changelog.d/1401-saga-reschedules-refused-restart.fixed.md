**core:** the recovery saga's restarts now run with default settings. The saga
scheduled its first restart 5s after a server degraded, and the server's own
backoff after three failures is about 8s, so the server refused the restart.
The refusal recorded nothing, nothing scheduled another restart, and the server
stayed `degraded` until a call happened to start it. A restart the server
refuses because its backoff has not elapsed is now scheduled again at the retry
time the server reports, and the refusal does not count as one of the saga's
attempts. The server's backoff stays the only clock that decides when a start
may run. A server that keeps refusing, for example with a retry time of 0, is
asked at most once a second and at most 20 times per attempt; after that the
refusals count as a failed attempt, so the saga still gives up. As before, the
saga gives up after `max_retries` failed restarts and leaves the server `dead`.
A timer that shutdown cancels while it is firing no longer schedules anything
after it.
