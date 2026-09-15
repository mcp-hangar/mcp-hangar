**core:** a tool call held for approval no longer leaves an event loop, an idle
thread and open file descriptors behind. The synchronous approval wait ran on an
event loop kept per worker thread and closed only at interpreter exit, and every
batch starts new worker threads, so nearly every held call left one loop, its
selector's descriptors and its default-executor thread alive until the process
exited. A gateway serving many approval-held calls grew its thread and
descriptor counts without bound. Each wait, and the re-validation after it, now
runs on a loop of its own that is shut down, with its executor thread joined,
before the call continues. Hold, approve, deny, time out and re-validation behave
as before.
