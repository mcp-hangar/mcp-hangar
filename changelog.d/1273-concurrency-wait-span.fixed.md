**core:** the `concurrency.acquire` span of a batch call now covers only the
time spent waiting for a free global and per-server concurrency slot, and ends
as soon as the slots are held. It used to stay open until the tool invocation
and every retry had finished, so a slow tool looked like a long wait for
capacity even when slots were free. The `invoke_with_retry` and
`command.send.InvokeToolCommand` spans are now children of `batch.call.<tool>`
instead of `concurrency.acquire`. The slots are still held until the invocation
returns, and `concurrency.wait_ms` and the other span attributes are unchanged
