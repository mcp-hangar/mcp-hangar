**core:** `config.yaml` now says something about a key nothing reads. It had no
schema, so unknown keys were kept and ignored at every level: `commandd:
[python]` built a subprocess server with no command, `idle_tt1_s: 60` applied
nothing, and `auth: {enabledd: true}` was a deployment that believed it had
enabled authentication. The failure surfaced later and elsewhere -- a subprocess
that will not start reads like a broken server, not a misspelled key. Top-level
section names, each section's own keys and `mcp_servers.<id>` spec keys are now
checked, and the message names the offending key and the allowed set, matching
what `domain/policies/dsl.py` already did for the policy DSL. This release
**warns**; `HANGAR_CONFIG_STRICT=1` refuses now and refusal becomes the default
in 3.0.0. New `mcp-hangar config check [path]` answers the same question without
starting a gateway, exiting 1 on an unknown key
