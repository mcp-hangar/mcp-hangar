**core:** four metrics that nothing has written to since #1002 are no longer
exposed: `mcp_hangar_behavioral_deviations_total`,
`mcp_hangar_tool_schema_drifts_total`, `mcp_hangar_detection_rule_matches_total`
and `mcp_hangar_enforcement_actions_total`. Each was a `# TYPE` header with no
sample, which reads to a dashboard exactly like a signal that is working and
quiet; the features that would feed them -- anomaly detection, behavioural
profiling and schema-drift detection -- are not shipped. `Metrics` is gone from
`mcp_hangar.observability` with them: a table of metric-name constants that
nothing read, seven of whose ten entries named no real family. The test meant
to catch all four now counts only reads that reach the metrics module, and no
longer counts the registration list as one -- either hole alone had kept it
green
