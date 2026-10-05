**core:** a refused connection now fails a remote server's health check. The HTTP
client reports one as `ClientError`, which `McpServer.health_check` did not catch:
it escaped to the health worker as `background_task_failed`, no failure was
recorded, and a stopped remote server stayed `ready`, in rotation, for as long as
the upstream was down. Every `ClientError` from the probe now counts as a failed
check, as a timeout already did: the server degrades after
`max_consecutive_failures`, and its group counts each failed check against its
own `health.unhealthy_threshold` and `circuit_breaker.failure_threshold`.
