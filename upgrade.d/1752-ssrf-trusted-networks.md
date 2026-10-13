### Trusting private networks for servers registered over the API

`POST /api/mcp_servers` still refuses every private endpoint by default. To let
the Kubernetes operator register a server it runs in the cluster, list the
cluster's Service range in `config.yaml`:

```yaml
ssrf:
  trusted_networks: ["10.96.0.0/12"]
```

- The list applies to every caller allowed to register a server, not only the
  operator. Keep it as narrow as the servers need.
- The Service range also holds the kube-apiserver's ClusterIP and every other
  Service in the cluster. Add the pod range only for headless Services.
- Loopback, link-local (including the cloud metadata endpoint) and the
  unspecified ranges are refused at startup and on reload. So is a CIDR with
  host bits set.
