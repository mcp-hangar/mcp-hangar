# 11 -- Discovery: Kubernetes

> **Prerequisite:** [01 -- HTTP Gateway](01-http-gateway.md)
> **You will need:** Running Hangar, Kubernetes cluster with the MCP-Hangar Operator
> **Time:** 15 minutes
> **Adds:** Auto-discover MCP providers from Kubernetes annotations

## The Problem

You run MCP providers as Kubernetes services. Teams deploy and scale providers independently. You need Hangar to discover them from annotations without manual config updates.

## The Config

```yaml
# config.yaml -- Recipe 11: Kubernetes Discovery
discovery:
  enabled: true
  refresh_interval_s: 30
  auto_register: true                    # NEW: trust K8s annotations

  sources:
    - type: kubernetes                   # NEW: Kubernetes source
      mode: authoritative                # NEW: add AND remove on pod changes
      config:                            # NEW: K8s-specific config
        namespace: "mcp-providers"       # NEW: watch this namespace
        label_selector: "app.kubernetes.io/part-of=mcp"  # NEW: filter pods
```

## Try It

1. Deploy an MCP provider with annotations:

   ```bash
   kubectl apply -f - <<EOF
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: math-provider
     namespace: mcp-providers
     labels:
       app.kubernetes.io/part-of: mcp
     annotations:
       mcp-hangar.io/enabled: "true"
       mcp-hangar.io/name: "k8s-math"
       mcp-hangar.io/port: "8080"
   spec:
     replicas: 2
     selector:
       matchLabels:
         app: math-provider
     template:
       metadata:
         labels:
           app: math-provider
       spec:
         containers:
           - name: math
             image: my-registry/math-server:latest
             ports:
               - containerPort: 8080
   EOF
   ```

2. Expose the deployment:

   ```bash
   kubectl expose deployment math-provider -n mcp-providers --port=8080
   ```

3. Verify Hangar discovers it:

   ```bash
   curl http://localhost:8000/api/discovery/sources
   ```

4. Check registered providers:

   ```bash
   mcp-hangar status
   ```

   ```
   k8s-math    remote    cold    source=kubernetes:auto-discovery
   ```

5. Scale up and watch Hangar adapt:

   ```bash
   kubectl scale deployment math-provider -n mcp-providers --replicas=3
   ```

## What Just Happened

The Kubernetes discovery source watches pods in the configured namespace matching the label selector. Pods with `mcp-hangar.io/enabled: "true"` annotations are registered as remote providers. In `authoritative` mode, when a pod is deleted, the corresponding provider is deregistered.

For declarative management, use the MCP-Hangar Operator CRDs instead. See the [Kubernetes guide](../guides/KUBERNETES.md).

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `discovery.sources[].type` | string | -- | Set to `kubernetes` |
| `discovery.sources[].mode` | string | -- | `additive` or `authoritative` |
| `discovery.sources[].config.namespace` | string | `default` | Kubernetes namespace to watch |
| `discovery.sources[].config.label_selector` | string | -- | Pod label selector |

### Kubernetes Annotations

| Annotation | Required | Default | Description |
|------------|----------|---------|-------------|
| `mcp-hangar.io/enabled` | Yes | -- | Must be `"true"` |
| `mcp-hangar.io/name` | No | Pod name | Provider name |
| `mcp-hangar.io/port` | No | `8080` | Provider port |
| `mcp-hangar.io/group` | No | -- | Auto-add to group |

## What's Next

You have discovery working. Now add authentication to control who can access your providers.

--> [12 -- Auth & RBAC](12-auth-rbac.md)
