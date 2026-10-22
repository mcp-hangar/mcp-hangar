### An endpoint in `fc00::/7` or `100.64.0.0/10` needs `ssrf.trusted_networks`

The strict SSRF policy now treats IPv6 unique local addresses (`fc00::/7`) and RFC 6598 shared address space (`100.64.0.0/10`) as private, like `10.0.0.0/8`. A server registered over the API whose endpoint resolves into either range is refused at registration, and one registered before the upgrade is refused at its next connection.

This includes the Kubernetes operator's registrations on a cluster whose pods or Services use these ranges, because the operator registers over the API. Servers declared in `config.yaml` are not checked by this policy and are not affected.

If such an endpoint is meant, trust its network:

```yaml
ssrf:
  trusted_networks: ["fd00:10:96::/112", "100.64.0.0/10"]
```

Servers found by discovery are not affected either.
