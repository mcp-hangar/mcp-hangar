**core:** a retried call now says which layer retried it, how often and how long
it waited. Two layers retry one call -- the executor's command send and the HTTP
client's resend -- and both reported totals only, so three upstream POSTs might
have been one executor attempt that resent twice or three executor attempts, and
a trace read the same either way.

Each `command.send.InvokeToolCommand` span carries its attempt index. Each
retried failure adds a `hangar.retry.attempt` event -- on `invoke_with_retry`
for the executor layer, on the existing upstream CLIENT span for the HTTP layer
-- naming the layer, the index, a bounded reason (an error type, an HTTP status
or `connection_error`) and the backoff about to be waited out. `invoke_with_retry`
ends with `hangar.retry.outcome`: `success`, `exhausted` or `non_retryable`.

Events rather than attributes, because one call has many attempts and an
attribute holds one value per key -- the last retry would erase every one before
it. `exhausted` and `non_retryable` are separated because they arrive looking
identical and mean opposite things: the upstream kept failing and the budget ran
out, or the first failure was never worth retrying.

No retry behaviour changes: max attempts, delays, `retry_on` rules, cancellation
and the returned envelopes are exactly as they were.
