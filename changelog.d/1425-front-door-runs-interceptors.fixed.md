**core:** a front door's flat `tools/call` now runs the configured
interceptors. It dispatched through a `BatchExecutor` it built for each call,
with an empty interceptor pipeline, so a validator configured under
`interceptors.validators`, such as `payload_size`, ran on `hangar_call` and did
not run when the same tool was called by its flat name. The flat call now runs
through the executor `hangar_call` runs, and a payload a validator refuses is
refused with the same error on both paths.
