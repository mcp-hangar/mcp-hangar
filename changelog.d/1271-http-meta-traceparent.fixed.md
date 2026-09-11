**core:** an HTTP upstream now receives, in `params._meta`, the `traceparent`
of the CLIENT span Hangar opens for that request, the same span the
`traceparent` header names, as over stdio. `_meta` was filled in before that
span opened, so it named the span that called into Hangar, or, when there was
none, carried no `traceparent` at all while the header did. HTTP notifications
are corrected the same way. While tracing is active, a `traceparent` already in
the caller's `_meta` is replaced by that span's rather than forwarded
