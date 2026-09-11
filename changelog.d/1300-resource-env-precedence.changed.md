**core:** resource attributes set through the standard OpenTelemetry
environment now win over Hangar's own on exported traces.
`OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod` now exports `prod`
instead of `development`; `MCP_ENVIRONMENT` (default `development`) still
applies when the environment sets none. The service name comes from
`OTEL_SERVICE_NAME`, then `service.name` in `OTEL_RESOURCE_ATTRIBUTES`, then
`observability.tracing.service_name` in config.yaml, then `mcp-hangar`. One
behaviour change: a `service.name` in `OTEL_RESOURCE_ATTRIBUTES` used to be
overridden and now beats the one in config.yaml. A server started by Hangar's
bootstrap now reports `service.instance.id` as the instance identity that domain
events carry in `produced_by`, instead of a random id from the OpenTelemetry SDK,
unless `OTEL_RESOURCE_ATTRIBUTES` sets one
