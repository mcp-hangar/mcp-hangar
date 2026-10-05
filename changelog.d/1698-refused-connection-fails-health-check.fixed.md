**core:** a refused connection now fails a remote server's health check. The HTTP
client reports one as `ClientError`, which `McpServer.health_check` did not catch:
it escaped to the health worker as `background_task_failed`, no failure was
recorded, and a stopped remote server stayed `ready` with its group's circuit
closed for as long as the upstream was down. Every `ClientError` from the probe
now counts as a failed check, so the server degrades after
`max_consecutive_failures` and its group takes it out of rotation and opens the
circuit, as it already did for an upstream that timed out.
