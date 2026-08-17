The #969 sweep: nine verified-unused surfaces left over from the factory cut
are deleted from `src/` -- the `HangarError`/`Rich*` error zoo with its
factories and `ErrorClassifier` (`is_retryable` stays), `ProgressTracker`,
the `HealthEndpoint` registry nothing served (event-store durability get/set
stays), `domain/bundles`, `AuditService`, the tenant/catalog/package
exception cluster with `McpServerEntry`/`CatalogItemId`,
`HangarLoadResult`/`HangarUnloadResult` and the unused REST serializers, the
never-called metrics helpers (`init_metrics`, `timed`, `record_*` for
unshipped detection features), and `initialize_runtime`/`shutdown_runtime`
plus the `trace_tool_invocation` decorator. None had a production caller;
see UPGRADE.md for the replacement surfaces.
