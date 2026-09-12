**core:** Hangar now bounds the length of the values it records. Span
attributes on Hangar's own tracer provider are cut at 256 characters, and so
are span event and link attributes, including an exception's message and
stack trace. Attributes of OTLP audit records sent through Hangar's own logger
provider are cut at 256, a string in a structured-log record at 2048, and a
free-text field of a domain event, such as `error_message`, `reason` or
`reasons`, at 4096. A cut value ends with `…[truncated N]`, and the marker
counts toward the limit. Span attributes are the exception: the OpenTelemetry
SDK cuts them and adds no marker. Log records are cut after secrets are
redacted, and a domain event is cut once, when it is constructed, so the event
store, `/ws/events`, the audit trail and the logs all hold the same value. Set
`MCP_SPAN_ATTRIBUTE_LENGTH_LIMIT`, `MCP_AUDIT_ATTRIBUTE_LENGTH_LIMIT`,
`MCP_LOG_FIELD_LENGTH_LIMIT` or `MCP_EVENT_TEXT_LENGTH_LIMIT` to change a
limit. `OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT`,
`OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT` and
`OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` win over Hangar's span and audit variables
when set. A tracer or logger provider registered before Hangar starts is not
reconfigured.
