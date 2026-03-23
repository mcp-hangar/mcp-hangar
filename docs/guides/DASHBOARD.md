# Dashboard UI

MCP Hangar includes a web dashboard for managing providers, groups, discovery sources, and observability -- all from a browser.

The dashboard is a React 19 + TypeScript application in `packages/ui/`, served by the Hangar HTTP server.

## Quick Start

### Development Mode

Run the dashboard with hot-reload for development:

```bash
cd packages/ui
npm install
npm run dev
```

The dashboard opens at `http://localhost:5173` and proxies API requests to `http://localhost:8000`.

Start the Hangar backend in another terminal:

```bash
mcp-hangar serve --http --port 8000
```

### Production Mode

In production, the dashboard is built as static files and served by the Hangar HTTP server directly:

```bash
# Build the UI
cd packages/ui
npm run build

# Start Hangar -- serves UI at / and API at /api/
mcp-hangar serve --http --port 8000
```

Or use the Docker image which includes the UI build:

```bash
docker run -p 8000:8000 -v $(pwd)/config.yaml:/app/config.yaml:ro \
  ghcr.io/mcp-hangar/mcp-hangar:latest
```

## Pages

### Dashboard

The landing page shows a high-level overview of the system:

- **Provider state distribution** -- Pie chart of COLD/READY/DEGRADED/DEAD states.
- **Metrics summary** -- Total tool calls, active providers, health check counts.
- **Live event feed** -- Real-time stream of domain events via WebSocket.
- **Alerts** -- Active alerts from the alert handler.

### Providers

List all registered providers with state badges, mode, and tool counts.

**Actions per provider:**

- Start / Stop
- View details (tools list, health status, configuration)
- View live logs (stderr stream via WebSocket)
- Create / Edit / Delete providers via drawers

**Provider Detail Page:**

- Configuration summary
- Tool list with invocation history
- Health check timeline
- Live log viewer with auto-scroll

### Groups

Manage provider groups with load balancing configuration.

- View group state, strategy, and member health
- Create / Edit / Delete groups
- Add / Remove members with weight and priority
- Trigger rebalance

### Discovery

Browse and manage auto-discovery sources.

- List active sources with last scan time and provider count
- Register new sources (Docker, Kubernetes, filesystem, entrypoint)
- Enable/disable sources
- Trigger manual scans
- Approve or reject pending providers

### Catalog

Browse the MCP provider catalog.

- Search by name or description
- Filter by tags
- Deploy catalog entries as live providers

### Topology

Visual graph of provider relationships using D3 force layout.

- Nodes represent providers and groups
- Edges show group membership
- Color-coded by provider state
- Interactive zoom and drag

### Metrics

Time-series charts of system metrics.

- Tool call rate over time
- Health check pass/fail rate
- Provider state transitions
- Circuit breaker state changes

Data comes from the metrics history store, which snapshots Prometheus counters at regular intervals.

### Security

RBAC management interface.

**Roles Tab:**

- List built-in and custom roles
- Create / Edit / Delete custom roles

**Principals Tab:**

- List principals with assigned roles
- Assign / Revoke roles
- Manage tool access policies per principal

### Config Export

Export and compare configuration.

- Export current in-memory configuration as YAML
- Colored diff viewer comparing on-disk vs in-memory state
- Create backup snapshots

## Configuration

The dashboard connects to the REST API. In development mode, configure the proxy target in `vite.config.ts`:

```typescript
server: {
  proxy: {
    '/api': 'http://localhost:8000',
    '/ws': {
      target: 'ws://localhost:8000',
      ws: true,
    },
  },
}
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `/api` | Base URL for the REST API |
| `VITE_WS_URL` | `/api/ws` | Base URL for WebSocket connections |

### Theme

The dashboard supports light and dark themes. The theme toggle is in the header. Theme preference is persisted in `localStorage` via a Zustand store.

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Framework | React 19 |
| Language | TypeScript |
| Build | Vite 6 |
| State | Zustand |
| Data fetching | TanStack Query |
| WebSocket | Custom hooks with exponential backoff |
| Charts | Recharts |
| Topology | D3 force simulation |
| Styling | Tailwind CSS |
| Testing | Vitest + React Testing Library |

## Development

```bash
cd packages/ui

# Install dependencies
npm install

# Development server with hot reload
npm run dev

# Type check
npm run typecheck

# Lint
npm run lint

# Run tests
npm run test

# Build for production
npm run build
```
