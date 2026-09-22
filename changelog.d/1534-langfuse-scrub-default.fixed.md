**core:** enabling Langfuse through the environment no longer ships raw tool
inputs and outputs by default. `LangfuseConfig` and the `config.yaml` path
scrubbed, sending only the keys of each payload, but `bootstrap/runtime.py`
defaulted `langfuse_scrub_inputs` and `langfuse_scrub_outputs` to `False` and
read `HANGAR_LANGFUSE_SCRUB_INPUTS` / `HANGAR_LANGFUSE_SCRUB_OUTPUTS` as an
opt-in, overriding the component's safe default. An operator who set
`HANGAR_LANGFUSE_ENABLED=true` and the credentials, and nothing else, sent
`input_params` and `output` to Langfuse in full.

Every scrub flag now reads one default, `SCRUB_PAYLOADS_BY_DEFAULT` in
`mcp_hangar.application.ports.observability`, which is `True`. The two
environment variables are an opt-out: only `false`, `0`, `no` or `off` turns
scrubbing off, and any other value keeps it on.
