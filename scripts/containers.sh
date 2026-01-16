#!/bin/bash
# MCP Hangar - Container Manager
# Usage: ./scripts/containers.sh [start|stop|status|scale N]

DATA_DIR="${MCP_DATA_DIR:-./data}"
PODMAN="${PODMAN_PATH:-podman}"

start_containers() {
    echo "🚀 Starting MCP containers..."

    mkdir -p "$DATA_DIR/memory" "$DATA_DIR/filesystem"
    chmod 777 "$DATA_DIR/memory" "$DATA_DIR/filesystem"

    local count=${1:-5}

    # Memory providers
    for i in $(seq 1 $count); do
        $PODMAN run -d --name mcp-memory-$i \
            --label mcp.hangar.enabled=true \
            --label mcp.hangar.name=memory-$i \
            --label mcp.hangar.mode=container \
            --label mcp.hangar.read-only=false \
            --label mcp.hangar.group=memory-cluster \
            --label "mcp.hangar.volumes=$DATA_DIR/memory:/app/data:rw" \
            localhost/mcp-memory:latest 2>/dev/null && echo "✅ memory-$i"
    done

    # Math providers
    for i in $(seq 1 $count); do
        $PODMAN run -d --name mcp-math-$i \
            --label mcp.hangar.enabled=true \
            --label mcp.hangar.name=math-container-$i \
            --label mcp.hangar.mode=container \
            --label mcp.hangar.read-only=false \
            --label mcp.hangar.group=math-container-cluster \
            localhost/mcp-math:latest 2>/dev/null && echo "✅ math-container-$i"
    done

    # Filesystem providers
    for i in $(seq 1 $count); do
        $PODMAN run -d --name mcp-filesystem-$i \
            --label mcp.hangar.enabled=true \
            --label mcp.hangar.name=filesystem-$i \
            --label mcp.hangar.mode=container \
            --label mcp.hangar.read-only=false \
            --label mcp.hangar.group=filesystem-cluster \
            --label "mcp.hangar.volumes=$DATA_DIR/filesystem:/data:rw" \
            localhost/mcp-filesystem:latest 2>/dev/null && echo "✅ filesystem-$i"
    done

    # Fetch providers
    for i in $(seq 1 $count); do
        $PODMAN run -d --name mcp-fetch-$i \
            --label mcp.hangar.enabled=true \
            --label mcp.hangar.name=fetch-$i \
            --label mcp.hangar.mode=container \
            --label mcp.hangar.read-only=false \
            --label mcp.hangar.group=fetch-cluster \
            localhost/mcp-fetch:latest 2>/dev/null && echo "✅ fetch-$i"
    done

    echo ""
    echo "📊 Total: $((count * 4)) containers started"
}

stop_containers() {
    echo "🛑 Stopping MCP containers..."
    $PODMAN rm -f $($PODMAN ps -aq --filter label=mcp.hangar.enabled=true) 2>/dev/null
    echo "✅ All MCP containers stopped"
}

status() {
    echo "📦 MCP Containers:"
    $PODMAN ps -a --filter label=mcp.hangar.enabled=true --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"
    echo ""
    echo "Total: $($PODMAN ps -a --filter label=mcp.hangar.enabled=true -q | wc -l | tr -d ' ') containers"
}

case "$1" in
    start)
        start_containers ${2:-5}
        ;;
    stop)
        stop_containers
        ;;
    status)
        status
        ;;
    scale)
        stop_containers
        start_containers ${2:-5}
        ;;
    *)
        echo "Usage: $0 {start|stop|status|scale} [count]"
        echo ""
        echo "Commands:"
        echo "  start [N]  - Start N instances of each provider type (default: 5)"
        echo "  stop       - Stop all MCP containers"
        echo "  status     - Show container status"
        echo "  scale N    - Stop all and start N instances of each type"
        exit 1
        ;;
esac
