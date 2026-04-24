# Cookbook

From zero to production in 13 recipes. Start at 01 and go in order, or jump to what you need.

## The Path (sequential)

Recipes 01-06 build on each other. Start at 01 and work through in order.

| # | Recipe | What it adds |
|---|--------|--------------|
| 01 | [HTTP Gateway](01-http-gateway.md) | Single MCP server behind Hangar |
| 02 | [Health Checks](02-health-checks.md) | Know when your MCP server is dead |
| 03 | [Circuit Breaker](03-circuit-breaker.md) | Stop hammering a failing MCP server |
| 04 | [Failover](04-failover.md) | Automatic switch to backup MCP server |
| 05 | [Load Balancing](05-load-balancing.md) | Distribute load across multiple MCP servers |
| 06 | [Rate Limiting](06-rate-limiting.md) | Protect MCP servers from overload |

## Standalone Recipes

These recipes can be done independently but assume basic Hangar setup from recipe 01.

| # | Recipe | Prerequisite |
|---|--------|--------------|
| 07 | [Observability: Metrics](07-observability-metrics.md) | 01 |
| 08 | [Observability: Langfuse](08-observability-langfuse.md) | 01 |
| 09 | [Subprocess MCP servers](09-subprocess-providers.md) | 01 |
| 10 | [Discovery: Docker](10-discovery-docker.md) | 01 |
| 11 | [Discovery: Kubernetes](11-discovery-kubernetes.md) | 01 |
| 12 | [Auth & RBAC](12-auth-rbac.md) | 01 |
| 13 | [Production Checklist](13-production-checklist.md) | 01-06 |

## How to Use This Cookbook

Each recipe follows the same structure:

1. **The Problem** — What pain you're solving
2. **The Config** — Complete, copy-pasteable configuration
3. **Try It** — Step-by-step commands with expected output
4. **What Just Happened** — Technical explanation
5. **Key Config Reference** — New configuration options
6. **What's Next** — Link to the next recipe

Config blocks show the COMPLETE file, not fragments. New additions are marked with `# NEW` comments.
