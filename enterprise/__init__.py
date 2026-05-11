"""MCP Hangar Enterprise features.

Enterprise features extend the core with:
- RBAC, API key auth, JWT/OIDC integration
- Tool Access Policy enforcement
- Durable event store persistence (SQLite, Postgres)
- Behavioral profiling and deviation detection
- Caller identity propagation and audit trails
- Compliance export (CEF, LEEF, JSON-lines)
- Cost attribution / FinOps
- Semantic analysis and detection rules
- Langfuse LLM observability integration

Enterprise modules depend on core interfaces:
    enterprise/ --> mcp_hangar.domain.contracts/
    enterprise/ --> mcp_hangar.application.ports/

Core MUST NEVER import from enterprise/.
"""
