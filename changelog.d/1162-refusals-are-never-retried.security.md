**core:** a refusal is no longer retryable. `should_retry` matched `retry_on`
as a substring of both the exception type name and its message, with no
exclusion for deliberate decisions -- so `retry_on: ["Error"]` covered every
denial there is, and even the stock list retried a denial whose message
contained "timeout" or "connection". The batch executor could therefore re-ask
an approval gate, or re-drive a denied egress decision, once per attempt.
Access denials, egress denials, approval-required holds, authn/authz failures,
rate limits and validation errors are now excluded before any policy is
consulted
