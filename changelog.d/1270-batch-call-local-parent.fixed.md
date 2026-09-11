**core:** each `hangar_call` invocation now shows up in a trace backend under
the request that made it: remote caller, the `tools/call hangar_call` server
span, `hangar_call`, `batch.execute`, then `batch.call.<tool>`. The
`batch.call.<tool>` span used to hang directly off the caller's span named in
`_meta.traceparent`, skipping `hangar_call` and `batch.execute`, and without a
valid `traceparent` it started a trace of its own. A `traceparent` in the
legacy `call.metadata` that names a different trace is now attached to
`batch.call.<tool>` as a span link instead of becoming its parent. Span names
and attributes are unchanged
