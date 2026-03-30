#!/usr/bin/env bash
# dev-workspace.sh -- Set up multi-repo development environment
#
# Usage:
#   ./scripts/dev-workspace.sh [setup|start|stop|status]
#
# Prerequisites:
#   - mcp-hangar repo cloned (this repo)
#   - hangar-cloud repo cloned as sibling: ../hangar-cloud
#   - Go 1.23+, Python 3.11+, Node 20+
#   - buf CLI (for proto generation): https://buf.build/docs/installation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PARENT_DIR="$(cd "$ROOT_DIR/.." && pwd)"
CLOUD_DIR="$PARENT_DIR/hangar-cloud"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[info]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
log_error() { echo -e "${RED}[error]${NC} $*"; }

# --------------------------------------------------------------------------
# setup: one-time workspace configuration
# --------------------------------------------------------------------------
cmd_setup() {
    log_info "Setting up multi-repo workspace..."

    # Check sibling repo
    if [ -d "$CLOUD_DIR" ]; then
        log_info "hangar-cloud found at $CLOUD_DIR"
    else
        log_warn "hangar-cloud not found at $CLOUD_DIR"
        log_warn "Clone it: git clone git@github.com:mcp-hangar/hangar-cloud.git $CLOUD_DIR"
        log_warn "Continuing with mcp-hangar only (OSS mode)."
    fi

    # Create go.work for cross-repo Go development
    if [ -d "$CLOUD_DIR" ]; then
        local GO_WORK="$PARENT_DIR/go.work"
        if [ ! -f "$GO_WORK" ]; then
            log_info "Creating go.work at $GO_WORK"
            cat > "$GO_WORK" << 'GOWORK'
go 1.23

use (
    ./mcp-hangar/packages/operator
    ./mcp-hangar/api
    ./hangar-cloud
)
GOWORK
            log_info "go.work created. Go modules will resolve locally."
        else
            log_info "go.work already exists at $GO_WORK"
        fi
    fi

    # Generate proto Go code
    if command -v buf &> /dev/null; then
        log_info "Generating proto code..."
        cd "$ROOT_DIR/api"
        buf generate
        log_info "Proto code generated in api/gen/go/"
    else
        log_warn "buf CLI not found. Install: https://buf.build/docs/installation"
        log_warn "Skipping proto generation."
    fi

    # Python dependencies
    log_info "Installing Python dependencies..."
    cd "$ROOT_DIR"
    if command -v uv &> /dev/null; then
        uv sync
    else
        pip install -e ".[dev]"
    fi

    log_info "Setup complete."
}

# --------------------------------------------------------------------------
# start: launch local development services
# --------------------------------------------------------------------------
cmd_start() {
    local mode="${1:-oss}"

    case "$mode" in
        oss)
            log_info "Starting in OSS mode (Python core only)..."
            cd "$ROOT_DIR"
            MCP_MODE=http MCP_HTTP_PORT=8000 MCP_LOG_LEVEL=DEBUG \
                python -m mcp_hangar.server.cli serve --http --port 8000 &
            echo $! > /tmp/mcp-hangar-core.pid
            log_info "Python core started on :8000 (PID: $(cat /tmp/mcp-hangar-core.pid))"
            ;;

        managed)
            log_info "Starting in managed mode (agent + core)..."

            # Start Python core in managed mode
            cd "$ROOT_DIR"
            MCP_MODE=http MCP_HTTP_PORT=8000 MCP_MANAGED=true MCP_LOG_LEVEL=DEBUG \
                python -m mcp_hangar.server.cli serve --http --port 8000 --managed &
            echo $! > /tmp/mcp-hangar-core.pid
            log_info "Python core (managed) started on :8000"

            # Start Go agent
            cd "$ROOT_DIR/packages/operator"
            HANGAR_URL=http://localhost:8000 \
                go run ./cmd/operator/main.go \
                --metrics-bind-address=:8081 \
                --health-probe-bind-address=:8082 &
            echo $! > /tmp/mcp-hangar-agent.pid
            log_info "Go agent started on :8081 (metrics) :8082 (health)"
            ;;

        full)
            if [ ! -d "$CLOUD_DIR" ]; then
                log_error "hangar-cloud not found. Cannot start full stack."
                exit 1
            fi

            log_info "Starting full stack (core + agent + cloud)..."

            # Start Python core in managed mode
            cmd_start managed

            # Start SaaS services
            cd "$CLOUD_DIR"
            if [ -f "docker-compose.dev.yml" ]; then
                docker compose -f docker-compose.dev.yml up -d
                log_info "SaaS services started (docker-compose)"
            else
                log_warn "No docker-compose.dev.yml in hangar-cloud. Start SaaS manually."
            fi
            ;;

        *)
            echo "Usage: $0 start [oss|managed|full]"
            exit 1
            ;;
    esac

    log_info "Development environment ready."
    cmd_status
}

# --------------------------------------------------------------------------
# stop: shut down all development services
# --------------------------------------------------------------------------
cmd_stop() {
    log_info "Stopping development services..."

    for pidfile in /tmp/mcp-hangar-core.pid /tmp/mcp-hangar-agent.pid; do
        if [ -f "$pidfile" ]; then
            local pid
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid"
                log_info "Stopped PID $pid ($(basename "$pidfile" .pid))"
            fi
            rm -f "$pidfile"
        fi
    done

    if [ -d "$CLOUD_DIR" ] && [ -f "$CLOUD_DIR/docker-compose.dev.yml" ]; then
        cd "$CLOUD_DIR"
        docker compose -f docker-compose.dev.yml down 2>/dev/null || true
    fi

    log_info "All services stopped."
}

# --------------------------------------------------------------------------
# status: show running services
# --------------------------------------------------------------------------
cmd_status() {
    echo ""
    echo "=== MCP Hangar Dev Workspace ==="
    echo ""

    # Python core
    if [ -f /tmp/mcp-hangar-core.pid ] && kill -0 "$(cat /tmp/mcp-hangar-core.pid)" 2>/dev/null; then
        echo -e "  Python Core:  ${GREEN}running${NC}  :8000  (PID $(cat /tmp/mcp-hangar-core.pid))"
    else
        echo -e "  Python Core:  ${RED}stopped${NC}"
    fi

    # Go agent
    if [ -f /tmp/mcp-hangar-agent.pid ] && kill -0 "$(cat /tmp/mcp-hangar-agent.pid)" 2>/dev/null; then
        echo -e "  Go Agent:     ${GREEN}running${NC}  :8081  (PID $(cat /tmp/mcp-hangar-agent.pid))"
    else
        echo -e "  Go Agent:     ${RED}stopped${NC}"
    fi

    # SaaS
    if [ -d "$CLOUD_DIR" ]; then
        echo -e "  hangar-cloud: ${GREEN}found${NC}   $CLOUD_DIR"
    else
        echo -e "  hangar-cloud: ${YELLOW}not cloned${NC}"
    fi

    # go.work
    if [ -f "$PARENT_DIR/go.work" ]; then
        echo -e "  go.work:      ${GREEN}active${NC}  $PARENT_DIR/go.work"
    else
        echo -e "  go.work:      ${YELLOW}not set up${NC}  (run: $0 setup)"
    fi

    echo ""
}

# --------------------------------------------------------------------------
# proto: regenerate proto Go code
# --------------------------------------------------------------------------
cmd_proto() {
    if ! command -v buf &> /dev/null; then
        log_error "buf CLI not found. Install: https://buf.build/docs/installation"
        exit 1
    fi

    log_info "Generating proto code..."
    cd "$ROOT_DIR/api"
    buf lint
    buf generate
    log_info "Proto code generated in api/gen/go/"

    # Check breaking changes against main branch
    if git rev-parse --verify origin/main &>/dev/null; then
        log_info "Checking for breaking proto changes..."
        buf breaking --against ".git#branch=main,subdir=api/proto"
        log_info "No breaking changes detected."
    fi
}

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
case "${1:-status}" in
    setup)  cmd_setup ;;
    start)  cmd_start "${2:-oss}" ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    proto)  cmd_proto ;;
    *)
        echo "Usage: $0 {setup|start|stop|status|proto}"
        echo ""
        echo "Commands:"
        echo "  setup              One-time workspace setup (go.work, deps, proto)"
        echo "  start [mode]       Start dev services (oss|managed|full)"
        echo "  stop               Stop all dev services"
        echo "  status             Show running services"
        echo "  proto              Regenerate proto Go code + lint + breaking check"
        exit 1
        ;;
esac

