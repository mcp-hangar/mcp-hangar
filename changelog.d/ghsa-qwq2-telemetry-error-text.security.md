**security:** traces, the security log and the Langfuse integration no longer
carry the text of a failed tool call, and Hangar no longer forwards W3C
baggage upstream (GHSA-qwq2-7g49-jxc6).

A failed `batch.call.<tool>` span used to take the call's error message as its
status description. Every other Hangar span recorded an escaping exception as
an `exception` event, message and stacktrace included, and the SDK set its
status description to `"<type>: <message>"`. That message can hold what an
upstream tool returned. Hangar's spans now end in ERROR with an empty status
description and a bounded `error.type`: the exception's class name, or the
upstream's JSON-RPC error code. The `exception` event is still recorded, but
its only attribute is `exception.type`, with no `exception.message` and no
`exception.stacktrace`. A type that does not look like a class name or a code
is recorded as `_OTHER`. The caller still gets the full error, and the event
store and `/ws/events` still keep `error_message`.

The security log now carries `error_type` instead of the error message for a
failed tool call, and no error text for repeated health-check failures. The
`health_check_failed` warning now logs the failure's `error_type` instead of its
text. The Langfuse integration sends a failed call's error type instead of its
message, in the span output, the status message and the `tool_success` score
comment. It still sends tool arguments and results.

An upstream JSON-RPC error whose `code` is not an integer is now recorded with
`error_type` `_OTHER`, OpenTelemetry's value for an unclassifiable error.
Before, `error_type` held the value sent, or `unknown` when there was no
`code`. The `error_type` stored in the event store for such errors changes
accordingly.

Hangar now propagates only `traceparent` and `tracestate`. It no longer reads
`baggage` from an inbound carrier, and no longer sends any upstream, over HTTP
or stdio. That includes baggage that host instrumentation attached to the
context and a `baggage` key in a request's `_meta`. Before, HTTP forwarded
entries whose keys started with `hangar.`, which any caller could write, and
stdio forwarded every entry.

`scrub_baggage_for_tenant` is removed from `mcp_hangar.observability`, with no
replacement. If you call it, delete the call. There is nothing to do instead:
Hangar forwards no baggage.
