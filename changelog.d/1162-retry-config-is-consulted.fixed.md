**core:** the `retry:` config section did nothing. It was parsed, merged and
logged at startup, and then no code read the store back: the batch executor
built its policy from the `hangar_batch` `max_attempts` argument alone, so
`backoff`, `initial_delay`, `max_delay`, `retry_on` and `jitter` were always
the class defaults and `per_mcp_server` applied to nothing. The executor now
retries under the configured policy, with the caller's `max_attempts` able to
lower the attempt count but not raise it; a deployment that configures nothing
is unchanged, at one attempt
