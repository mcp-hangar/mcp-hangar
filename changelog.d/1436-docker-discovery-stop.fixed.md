**core:** stopping `serve`, or a `Hangar` that runs discovery, no longer waits
out the docker discovery source's connection backoff while Docker is
unreachable. The retry slept on discovery's own event loop, so the stop could
not run until the remaining retries had finished: up to about 15 s with the
defaults. The wait now ends as soon as the stop begins, and no further
connection attempts are made. `DiscoverySource` has a new optional, synchronous
`request_stop()` hook, called from the stopping thread before `stop()`; a
custom source that blocks discovery's loop in the same way can override it to
end its wait.
