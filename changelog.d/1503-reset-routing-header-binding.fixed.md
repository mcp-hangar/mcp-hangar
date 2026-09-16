**core:** a batch no longer leaves its routing headers bound on the caller's
context. `BatchExecutor.execute` binds this request's `Mcp-Param-*` routing
headers and its negotiated protocol version so a worker thread can read them,
and now resets both when the batch ends, on every path including a raise. Both
callers reach the executor on a copied context that is discarded, so what a call
routed on was never read by another call; a caller that runs the executor on a
context it keeps now gets the same guarantee. `set_current_protocol_negotiation`
returns a reset token, and `reset_current_protocol_negotiation` puts back what
was bound before it.
