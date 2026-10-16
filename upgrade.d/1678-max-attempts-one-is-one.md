### `hangar_call(max_attempts=1)` disables the configured retries

With a `retry:` block configured, `max_attempts=1` used to get the configured count anyway. It now means one attempt. A caller that passed `1` explicitly and relied on the configured retries (rather than omitting the argument) now gets a single attempt; omit `max_attempts` to keep the configured policy. The argument's default in the tool schema changes from `1` to `null`, which means "the configured policy". Nothing changes for a gateway without a `retry:` block.
