**core:** An invalid `OTEL_TRACES_SAMPLER_ARG` no longer turns tracing off.
With `OTEL_TRACES_SAMPLER=traceidratio` or `parentbased_traceidratio`, a
ratio outside 0 to 1, or one that is not a number, made tracing
initialization fail with a single `tracing_initialization_failed` error, and
the process then ran with no tracing at all. Now Hangar logs one
`tracing_sampler_arg_invalid` warning that names the variable and its value,
samples every trace (ratio 1.0, the value when the variable is unset), and
keeps tracing on. A non-numeric value already fell back to 1.0, but without
a warning. Valid values behave as before.

Hangar now logs `tracing_initialized` once, listing the exporters it actually
attached, for example `exporters=["otlp_grpc", "console"]`. It used to log
the event twice: once with a count of exporters, and once with only the
service name. The line names no endpoint and no headers
