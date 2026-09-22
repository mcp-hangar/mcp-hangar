### Langfuse scrubs tool inputs and outputs by default on the environment path too

If you enable Langfuse with `HANGAR_LANGFUSE_ENABLED=true`, tool inputs and
outputs are now scrubbed unless you say otherwise: Langfuse receives
`{"scrubbed": true, "keys": [...]}` in place of `input_params` and `output`
(for an output that is not a mapping, its type in place of its keys).
Before this release that path sent both in full, because it defaulted
`HANGAR_LANGFUSE_SCRUB_INPUTS` and `HANGAR_LANGFUSE_SCRUB_OUTPUTS` to `false`,
overriding the scrubbing default that `LangfuseConfig` and the `config.yaml`
example already stated.

Deployments that configure Langfuse through `config.yaml` or the
`MCP_LANGFUSE_*` variables already scrubbed by default and see no change.

**If you rely on raw payloads reaching Langfuse,** opt out explicitly:

```bash
# before -- raw payloads were sent with nothing set
HANGAR_LANGFUSE_ENABLED=true

# after -- say so deliberately
HANGAR_LANGFUSE_ENABLED=true
HANGAR_LANGFUSE_SCRUB_INPUTS=false
HANGAR_LANGFUSE_SCRUB_OUTPUTS=false
```

Only `false`, `0`, `no` or `off` opts out. Any other value, including a typo,
keeps scrubbing on.
