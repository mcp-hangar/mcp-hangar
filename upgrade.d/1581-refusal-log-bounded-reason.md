### `batch_call_refused` from a gate no longer carries `error`

Since 2.22.0 a call refused by a batch gate logged `batch_call_refused` with an
`error` field holding the message the caller was told, which could be an
approver's own reason. That field is gone. A log query or alert that matches on
the text of `error` in these lines finds nothing after the upgrade: match on
`reason` (a bounded code such as `approval_denied` or `tool_withdrawn`), `gate`
or `error_type` instead, which are unchanged. The refusal message still reaches
the caller in the tool result, and approval decisions keep their reason in the
event store.
