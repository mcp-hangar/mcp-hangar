# security/ — Runtime Security Assets

This directory contains security profiles, templates, and policies used by
the MCP Hangar operator and provider runtime.

## Contents

```
security/
├── seccomp/
│   └── mcp-server-default.json   # Default seccomp profile for MCP provider containers
├── networkpolicy/
│   └── mcp-provider-deny-all.yaml  # Default deny-all NetworkPolicy template
└── apparmor/
    └── (coming in v0.13.0)         # AppArmor profiles for MCP providers
```

## Usage

### Seccomp profile

Apply to a Docker provider:

```bash
docker run --security-opt seccomp=security/seccomp/mcp-server-default.json \
  my-mcp-server:latest
```

Apply via Kubernetes pod spec (set by operator from `capabilities.enforcement_mode = "block"`):

```yaml
securityContext:
  seccompProfile:
    type: Localhost
    localhostProfile: mcp-server-default.json
```

### NetworkPolicy

The default deny-all template is applied by the operator to all providers
that do not have explicit `capabilities.network.egress` declarations.

Providers with declared egress get a per-provider NetworkPolicy generated
from their capability declaration.

## Operator Integration

The Hangar operator (Go, `operator/` repo) reads provider capability
declarations and reconciles:

1. `NetworkPolicy` resources from `capabilities.network.egress`
2. Pod security context from `capabilities.enforcement_mode`
3. Violation events when runtime behavior deviates from declarations

See `PRODUCT_ARCHITECTURE.md` Phase 1 for the full enforcement roadmap.
