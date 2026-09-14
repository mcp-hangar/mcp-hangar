**security:** Hangar now closes an upstream's transport client whenever it
stops holding it (GHSA-6fp8-g5qw-73g7).

A failed start used to leave the client it created open: for a stdio upstream a
child process and its pipes, for a remote upstream an HTTP client. A successful
start, such as a restart of a server that health checks had degraded, replaced
the server's client without closing the old one. Both accumulated for the life
of the gateway, so process, file-descriptor and connection counts grew with each
failed start and each restart.

A failed start now closes its client on every failure path, whether it leaves
the server `degraded` or `dead`: a failed handshake, a failure while collecting
startup diagnostics, and a failure after the upstream was launched but before
its client was ready. A call that revives a `dead` server closes every client
but the one it keeps. A successful start closes the client it replaces. A close
that fails is logged by its exception type and does not replace the start's own
error.
